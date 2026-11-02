# ABOUTME: Newsletter inbox: turns forwarded mails (raw MIME stored by SES) into articles.
# ABOUTME: Links are decoded offline and reduced to a clean post URL; tracking/token links are never kept.

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from feed_brain.services.extractor import extract_from_html
from feed_brain.textutil import clean_text

EMAIL_FEED_TYPE = "email"
NEWSLETTER_FOLDER = "Newsletter"
FALLBACK_SOURCE = "Newsletter"
GMAIL_FORWARDING_SENDER = "forwarding-noreply@google.com"

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
    """First link that is a newsletter post page, without query string or fragment."""
    for href in links:
        parts = urlsplit(decode_redirect(href))
        if parts.scheme not in ("http", "https") or not parts.netloc:
            continue
        if parts.netloc == "open.substack.com":
            match = _OPEN_SUBSTACK_POST.match(parts.path)
            if match:
                return f"https://{match[1]}.substack.com/p/{match[2]}"
            continue
        if parts.netloc != "substack.com" and _SUBSTACK_POST.match(parts.path):
            return f"https://{parts.netloc}{parts.path.rstrip('/')}"
    return None


def _lines(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _html_text(html: str) -> str:
    return _lines(BeautifulSoup(html, "html.parser").get_text("\n"))


def mail_text(html: str | None, plain: str | None) -> str:
    """Readable text of a newsletter: the extracted article when it holds most of the text
    (long-form posts), the whole body otherwise (link digests defeat readability). No URLs."""
    if html:
        whole = _html_text(html)
        main = _html_text(extract_from_html(html) or "")
        text = main if len(main) >= len(whole) / 2 else whole
    else:
        text = _lines(plain or "")
    return re.sub(r"<?https?://\S+>?", "", text).strip()


def _body(msg: EmailMessage, subtype: str) -> str | None:
    part = msg.get_body(preferencelist=(subtype,))
    if part is None:
        return None
    content = part.get_content()
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
    anchors = BeautifulSoup(html, "html.parser").find_all("a") if html else []
    links = [href for a in anchors if isinstance(href := a.get("href"), str)]
    content = clean_text(mail_text(html, _body(msg, "plain")))
    if not content:
        raise SkipMail("empty")

    subject = str(msg.get("Subject", ""))
    title = strip_forward_prefix(subject) or "Newsletter"
    message_id = str(msg.get("Message-ID", "")).strip()
    url = post_url(links) or f"mid:{message_id.strip('<>')}"

    if title != subject.strip():
        # Forwarded by hand: From is the forwarder, so name the source after the publication.
        host = urlsplit(url).netloc if url.startswith("http") else ""
        source_name = source_key = host or FALLBACK_SOURCE
    else:
        # Sent or auto-forwarded: Gmail filters keep the original From.
        source_name = name or address or FALLBACK_SOURCE
        source_key = (address or source_name).lower()

    try:
        published = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
    except (TypeError, ValueError):
        published = None

    return InboxMail(
        title=clean_text(title)[:500],
        source_name=clean_text(source_name)[:255],
        source_key=source_key[:200],
        url=url[:2048],
        content=content,
        published=published,
        message_id=message_id,
    )
