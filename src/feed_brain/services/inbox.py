# ABOUTME: Newsletter inbox: turns forwarded mails (raw MIME stored by SES in S3) into articles.
# ABOUTME: Links are decoded offline and reduced to a clean post URL; tracking/token links are never kept.

import asyncio
import base64
import binascii
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Protocol, Self
from urllib.parse import urlsplit

import boto3
import structlog
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from feed_brain.config import Settings
from feed_brain.db.models import EMAIL_FEED_TYPE, Article, FeedSource
from feed_brain.db.session import get_session_factory
from feed_brain.services.extractor import extract_from_html
from feed_brain.textutil import clean_text

log = structlog.get_logger()

NEWSLETTER_FOLDER = "Newsletter"
FALLBACK_SOURCE = "Newsletter"
GMAIL_FORWARDING_SENDER = "forwarding-noreply@google.com"
MAIL_PREFIX = "mail/"
# Written by SES when the receipt rule is created; not a mail.
SES_SETUP_OBJECT = f"{MAIL_PREFIX}AMAZON_SES_SETUP_NOTIFICATION"

_FORWARD_PREFIX = re.compile(r"^(?:\s*(?:fwd?|i|inoltro)\s*:)+\s*", re.IGNORECASE)
_SUBSTACK_POST = re.compile(r"^/p/[^/]+/?$")
_OPEN_SUBSTACK_POST = re.compile(r"^/pub/([^/]+)/p/([^/]+)/?$")


class SkipMail(Exception):  # noqa: N818 - a control-flow signal, not an error
    """A mail that must not become an article; `keep` leaves it in the bucket."""

    def __init__(self, reason: str, *, keep: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.keep = keep


@dataclass(frozen=True)
class InboxMail:
    """One newsletter issue, ready to be stored as an article."""

    title: str
    source_name: str
    source_key: str  # stable identity of the sender: feed_sources.url = "email:<key>"
    url: str  # clean public post URL, or "mid:<message-id>" when the mail has none
    content: str
    published: datetime | None
    message_id: str


def strip_forward_prefix(subject: str) -> str:
    """Drop leading Fwd:/FW:/I:/Inoltro: markers added by mail clients."""
    return _FORWARD_PREFIX.sub("", subject).strip()


def _b64json(segment: str) -> dict | None:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def decode_redirect(href: str) -> str:
    """Destination of a newsletter click-tracking link, read from its payload without following it.

    Knows Substack (`substack.com/redirect/2/<base64 json>`, key `e`) and Drip
    (`*.dripemail2.com/c/<jwt>`, claim `url`). Anything else is returned unchanged.
    """
    parts = urlsplit(href)
    segments = parts.path.split("/")
    payload: dict | None = None
    if (
        parts.netloc == "substack.com"
        and parts.path.startswith("/redirect/2/")
        and len(segments) > 3
    ):
        payload = _b64json(segments[3].split(".")[0])
        destination = payload.get("e") if payload else None
    elif (
        parts.netloc.endswith("dripemail2.com")
        and parts.path.startswith("/c/")
        and len(segments) > 2
    ):
        token = segments[2].split(".")
        payload = _b64json(token[1]) if len(token) == 3 else None
        destination = payload.get("url") if payload else None
    else:
        return href
    if isinstance(destination, str) and destination.startswith(("https://", "http://")):
        return destination
    return href


def post_url(links: list[str]) -> str | None:
    """The issue's own post page, without query string or fragment.

    Substack mails link their own post through open.substack.com share/comment links; a
    /p/<slug> link with the same slug (custom domain) is preferred to the substack.com form.
    Elsewhere a /p/<slug> link counts only when the mail repeats it, since a single one is
    usually a cited article; links unwrapped from Drip (digests of others' articles) never do.
    """
    opened: tuple[str, str] | None = None
    posts: Counter[str] = Counter()
    for href in links:
        unwrapped_from_drip = urlsplit(href).netloc.endswith("dripemail2.com")
        parts = urlsplit(decode_redirect(href))
        if parts.scheme not in ("http", "https") or not parts.netloc:
            continue
        if parts.netloc == "open.substack.com":
            match = _OPEN_SUBSTACK_POST.match(parts.path)
            if match and opened is None:
                opened = (match[1], match[2])
        elif (
            parts.netloc != "substack.com"
            and _SUBSTACK_POST.match(parts.path)
            and not unwrapped_from_drip
        ):
            posts[f"https://{parts.netloc}{parts.path.rstrip('/')}"] += 1
    if opened is not None:
        pub, slug = opened
        same_post = next((url for url in posts if url.rsplit("/", 1)[-1] == slug), None)
        return same_post or f"https://{pub}.substack.com/p/{slug}"
    return next((url for url, seen in posts.most_common() if seen >= 2), None)


def _lines(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _html_text(html: str) -> str:
    return _lines(BeautifulSoup(html, "html.parser").get_text("\n"))


def _without_urls(text: str) -> str:
    return re.sub(r"<?https?://\S+>?", "", text).strip()


def mail_text(html: str | None, plain: str | None) -> str:
    """Readable text of a newsletter, without URLs: the extracted article when it holds most
    of the text (long-form posts), the whole HTML body otherwise (link digests defeat
    readability), the plain part when the HTML has no text at all."""
    text = ""
    if html:
        whole = _html_text(html)
        main = _html_text(extract_from_html(html) or "")
        text = _without_urls(main if len(main) >= len(whole) / 2 else whole)
    return text or _without_urls(_lines(plain or ""))


def _body(msg: EmailMessage, subtype: str) -> str | None:
    part = msg.get_body(preferencelist=(subtype,))
    if part is None:
        return None
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):  # unknown or wrong charset label
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else None
    return content if isinstance(content, str) else None


def parse_mail(raw: bytes) -> InboxMail:
    """Parse one stored mail. Raises SkipMail for spam, Gmail forwarding confirmations and
    mails without text."""
    msg = message_from_bytes(raw, policy=policy.default)
    assert isinstance(msg, EmailMessage)

    for header, reason in (("X-SES-Spam-Verdict", "spam"), ("X-SES-Virus-Verdict", "virus")):
        if str(msg.get(header, "")).upper() == "FAIL":
            raise SkipMail(reason)

    name, address = parseaddr(str(msg.get("From", "")))
    if address.lower() == GMAIL_FORWARDING_SENDER:
        raise SkipMail("gmail_forwarding_confirmation", keep=True)

    html = _body(msg, "html")
    plain = _body(msg, "plain")
    anchors = BeautifulSoup(html, "html.parser").find_all("a") if html else []
    links = [href for a in anchors if isinstance(href := a.get("href"), str)]
    if not links and plain:
        links = re.findall(r"https?://[^\s<>\"')]+", plain)
    content = clean_text(mail_text(html, plain))
    if not content:
        raise SkipMail("empty")

    subject = str(msg.get("Subject", "") or "")
    forwarded = bool(_FORWARD_PREFIX.match(subject))
    title = strip_forward_prefix(subject) or "Newsletter"
    message_id = str(msg.get("Message-ID", "")).strip()
    # Without a Message-ID the content hash keeps distinct mails distinct (and re-reads stable).
    mail_id = message_id.strip("<> \t") or f"sha256-{hashlib.sha256(raw).hexdigest()[:32]}"
    url = post_url(links) or f"mid:{mail_id}"

    if forwarded:
        # Forwarded by hand: From is the forwarder, so name the source after the publication.
        host = urlsplit(url).netloc if url.startswith("http") else ""
        source_name = host or FALLBACK_SOURCE
        source_key = source_name.lower()
    else:
        # Sent or auto-forwarded: Gmail filters keep the original From.
        source_name = name or address or FALLBACK_SOURCE
        source_key = (address or source_name).lower()

    try:
        published = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
    except (TypeError, ValueError):
        published = None
    if published is not None and published.tzinfo is None:
        published = published.replace(tzinfo=UTC)  # "-0000" or no zone: UTC per RFC 5322

    return InboxMail(
        title=clean_text(title)[:500],
        source_name=clean_text(source_name)[:255],
        source_key=source_key[:200],
        url=url[:2048],
        content=content,
        published=published,
        message_id=message_id,
    )


# --- Mailbox (S3) ---


class Mailbox(Protocol):
    def keys(self, limit: int) -> list[str]:
        """Up to `limit` stored mails, oldest first."""
        ...

    def read(self, key: str) -> bytes:
        """Raw MIME of one mail."""
        ...

    def delete(self, key: str) -> None:
        """Remove a processed mail."""
        ...


def _google_id_token(audience: str) -> str:
    """ID token of the runtime's Google identity (the job service account on Cloud Run)."""
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    return id_token.fetch_id_token(Request(), audience)


def s3_client(settings: Settings) -> Any:
    """S3 client for the inbox: keyless web-identity federation when a role is configured,
    the default AWS credential chain (e.g. AWS_PROFILE on the Mac) otherwise."""
    if not settings.inbox_role_arn:
        return boto3.client("s3", region_name=settings.inbox_region)
    sts = boto3.client("sts", region_name=settings.inbox_region)
    credentials = sts.assume_role_with_web_identity(
        RoleArn=settings.inbox_role_arn,
        RoleSessionName="feed-brain-job",
        WebIdentityToken=_google_id_token(settings.inbox_audience),
        DurationSeconds=900,
    )["Credentials"]
    return boto3.client(
        "s3",
        region_name=settings.inbox_region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


class S3Mailbox:
    """Mails stored by the SES receipt rule under `mail/` in the inbox bucket."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        if not settings.inbox_bucket:
            raise ValueError("INBOX_BUCKET is not set")
        return cls(s3_client(settings), settings.inbox_bucket)

    def keys(self, limit: int) -> list[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        objects = [
            obj
            for page in paginator.paginate(Bucket=self.bucket, Prefix=MAIL_PREFIX)
            for obj in page.get("Contents", [])
            if obj["Key"] != SES_SETUP_OBJECT
        ]
        objects.sort(key=lambda obj: obj["LastModified"])
        return [obj["Key"] for obj in objects[:limit]]

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


# --- Ingest ---


@dataclass
class InboxStats:
    stored: int = 0
    duplicates: int = 0
    skipped: int = 0  # dropped: spam, virus, empty
    kept: int = 0  # left in the bucket on purpose (Gmail forwarding confirmation)
    failed: int = 0  # unparseable, left for the next run


async def _store(session: AsyncSession, mail: InboxMail) -> bool:
    """Insert the mail as a new article under its email source; False if the URL is known."""
    if (await session.execute(select(Article.id).where(Article.url == mail.url))).first():
        return False
    source_url = f"email:{mail.source_key}"
    source = (
        await session.execute(select(FeedSource).where(FeedSource.url == source_url))
    ).scalar_one_or_none()
    if source is None:
        source = FeedSource(
            name=mail.source_name,
            url=source_url,
            feed_type=EMAIL_FEED_TYPE,
            folder=NEWSLETTER_FOLDER,
        )
        session.add(source)
        await session.flush()
    session.add(
        Article(
            url=mail.url,
            title=mail.title,
            source_id=source.id,
            content=mail.content,
            published_date=mail.published,
            status="new",
            fetched_at=datetime.now(UTC),
        )
    )
    return True


async def ingest_inbox(mailbox: Mailbox, settings: Settings, *, delete: bool = True) -> InboxStats:
    """Store new mails as articles, oldest first, at most `inbox_max_per_run`.

    A mail leaves the bucket only once its article is committed (or it is a duplicate or
    a dropped skip); with delete=False (dry runs) every mail stays.
    """
    stats = InboxStats()
    factory = get_session_factory()
    for key in await asyncio.to_thread(mailbox.keys, settings.inbox_max_per_run):
        try:
            raw = await asyncio.to_thread(mailbox.read, key)
            mail = await asyncio.to_thread(parse_mail, raw)
        except SkipMail as e:
            log.info("inbox_skipped", key=key, reason=e.reason, kept=e.keep)
            if e.keep:
                stats.kept += 1
                continue
            stats.skipped += 1
        except Exception as e:  # noqa: BLE001 - one bad mail must not block the rest
            log.error("inbox_parse_failed", key=key, error=str(e)[:200])
            stats.failed += 1
            continue
        else:
            try:
                async with factory() as session:
                    stored = await _store(session, mail)
                    await session.commit()
            except SQLAlchemyError as e:
                log.error("inbox_store_failed", key=key, error=str(e)[:200])
                stats.failed += 1
                continue
            if stored:
                stats.stored += 1
                log.info("inbox_stored", key=key, source=mail.source_name, title=mail.title[:80])
            else:
                stats.duplicates += 1
        if delete:
            await asyncio.to_thread(mailbox.delete, key)
    log.info("inbox_done", **asdict(stats))
    return stats
