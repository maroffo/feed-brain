# ABOUTME: Tests for storing inbox mails as articles: idempotency, skips, dry runs, AWS credentials.
# ABOUTME: A dict-backed fake mailbox replaces S3; the DB is the in-memory SQLite session_factory.

from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from feed_brain.config import Settings
from feed_brain.db.models import Article, FeedSource
from feed_brain.services import inbox
from feed_brain.services.inbox import EMAIL_FEED_TYPE, NEWSLETTER_FOLDER, ingest_inbox
from tests.test_inbox import make_mail, substack_html


class FakeMailbox:
    def __init__(self, mails: dict[str, bytes]) -> None:
        self.mails = dict(mails)
        self.deleted: list[str] = []

    def keys(self, limit: int) -> list[str]:
        return list(self.mails)[:limit]

    def read(self, key: str) -> bytes:
        return self.mails[key]

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        del self.mails[key]


def post_mail(slug: str, sender: str = "Roberto Burioni <burioni@substack.com>") -> bytes:
    return make_mail(
        html=substack_html(f"https://burioni.substack.com/p/{slug}?token=x"),
        subject=f"Post {slug}",
        sender=sender,
    )


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


async def count(session_factory, model) -> int:
    async with session_factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_mails_become_articles_under_one_email_source_and_leave_the_bucket(session_factory):
    box = FakeMailbox({"mail/a": post_mail("uno"), "mail/b": post_mail("due")})

    stats = await ingest_inbox(box, settings())

    assert (stats.stored, stats.duplicates) == (2, 0)
    assert box.mails == {}
    async with session_factory() as session:
        sources = (await session.execute(select(FeedSource))).scalars().all()
        articles = (await session.execute(select(Article))).scalars().all()
    assert [(s.url, s.name, s.feed_type, s.folder) for s in sources] == [
        ("email:burioni@substack.com", "Roberto Burioni", EMAIL_FEED_TYPE, NEWSLETTER_FOLDER)
    ]
    assert sorted(a.url for a in articles) == [
        "https://burioni.substack.com/p/due",
        "https://burioni.substack.com/p/uno",
    ]
    assert all(a.status == "new" and a.source_id == sources[0].id for a in articles)


async def test_a_mail_seen_twice_is_stored_once(session_factory):
    await ingest_inbox(FakeMailbox({"mail/a": post_mail("uno")}), settings())
    box = FakeMailbox({"mail/b": post_mail("uno")})

    stats = await ingest_inbox(box, settings())

    assert (stats.stored, stats.duplicates) == (0, 1)
    assert box.deleted == ["mail/b"]
    assert await count(session_factory, Article) == 1


@pytest.mark.usefixtures("session_factory")
async def test_dry_run_stores_but_leaves_the_mail_in_the_bucket():
    box = FakeMailbox({"mail/a": post_mail("uno")})

    stats = await ingest_inbox(box, settings(), delete=False)

    assert stats.stored == 1
    assert box.deleted == []


@pytest.mark.usefixtures("session_factory")
async def test_skips_drop_spam_and_keep_gmail_confirmations():
    spam = make_mail(
        html=substack_html("https://a.substack.com/p/x"), extra={"X-SES-Spam-Verdict": "FAIL"}
    )
    confirmation = make_mail(
        html="<p>Confirmation code: 123456789</p>",
        subject="(#123456789) Gmail Forwarding Confirmation",
        sender="Gmail Team <forwarding-noreply@google.com>",
    )
    box = FakeMailbox({"mail/spam": spam, "mail/confirm": confirmation})

    stats = await ingest_inbox(box, settings())

    assert (stats.skipped, stats.kept, stats.stored) == (1, 1, 0)
    assert box.deleted == ["mail/spam"]
    assert list(box.mails) == ["mail/confirm"]


@pytest.mark.usefixtures("session_factory")
async def test_an_unparseable_mail_stays_and_does_not_block_the_others(monkeypatch):
    real_parse = inbox.parse_mail

    def parse(raw: bytes):
        if raw == b"broken":
            raise RuntimeError("bad MIME")
        return real_parse(raw)

    monkeypatch.setattr(inbox, "parse_mail", parse)
    box = FakeMailbox({"mail/bad": b"broken", "mail/ok": post_mail("uno")})

    stats = await ingest_inbox(box, settings())

    assert (stats.failed, stats.stored) == (1, 1)
    assert list(box.mails) == ["mail/bad"]


@pytest.mark.usefixtures("session_factory")
async def test_at_most_max_per_run_mails_are_read():
    box = FakeMailbox({f"mail/{i}": post_mail(f"p{i}") for i in range(5)})

    stats = await ingest_inbox(box, settings(inbox_max_per_run=2))

    assert stats.stored == 2
    assert len(box.mails) == 3


def test_s3_mailbox_lists_oldest_first_without_the_ses_setup_object():
    client = MagicMock()
    paginator = client.get_paginator.return_value
    paginator.paginate.return_value = [
        {
            "Contents": [
                {"Key": "mail/new", "LastModified": 3},
                {"Key": "mail/AMAZON_SES_SETUP_NOTIFICATION", "LastModified": 0},
                {"Key": "mail/old", "LastModified": 1},
            ]
        },
        {},
    ]
    box = inbox.S3Mailbox(client, "bucket")

    assert box.keys(10) == ["mail/old", "mail/new"]
    assert box.keys(1) == ["mail/old"]
    paginator.paginate.assert_called_with(Bucket="bucket", Prefix="mail/")


def test_s3_client_federates_the_job_identity_when_a_role_is_configured(monkeypatch):
    clients: list[tuple[str, dict]] = []
    sts = MagicMock()
    sts.assume_role_with_web_identity.return_value = {
        "Credentials": {"AccessKeyId": "AK", "SecretAccessKey": "SK", "SessionToken": "ST"}
    }

    def fake_client(service, **kwargs):
        clients.append((service, kwargs))
        return sts if service == "sts" else MagicMock()

    monkeypatch.setattr(inbox.boto3, "client", fake_client)
    monkeypatch.setattr(inbox, "_google_id_token", lambda audience: f"id-token-for-{audience}")

    inbox.s3_client(
        settings(
            inbox_role_arn="arn:aws:iam::1:role/r", inbox_region="eu-west-1", inbox_audience="aud"
        )
    )

    sts.assume_role_with_web_identity.assert_called_once()
    call = sts.assume_role_with_web_identity.call_args.kwargs
    assert (call["RoleArn"], call["WebIdentityToken"]) == (
        "arn:aws:iam::1:role/r",
        "id-token-for-aud",
    )
    assert clients[-1] == (
        "s3",
        {
            "region_name": "eu-west-1",
            "aws_access_key_id": "AK",
            "aws_secret_access_key": "SK",
            "aws_session_token": "ST",
        },
    )


def test_s3_client_uses_the_default_aws_chain_without_a_role(monkeypatch):
    clients: list[tuple[str, dict]] = []
    monkeypatch.setattr(inbox.boto3, "client", lambda service, **kw: clients.append((service, kw)))
    monkeypatch.setattr(inbox, "_google_id_token", pytest.fail)

    inbox.s3_client(settings(inbox_region="eu-west-1"))

    assert clients == [("s3", {"region_name": "eu-west-1"})]
