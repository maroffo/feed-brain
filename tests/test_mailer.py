# ABOUTME: Tests for the SES SMTP mailer: message structure, STARTTLS/login, missing config.
# ABOUTME: smtplib.SMTP is replaced by a recording fake; nothing leaves the machine.

import pytest
from pydantic import SecretStr

from feed_brain.config import Settings
from feed_brain.services import mailer
from feed_brain.services.mailer import MailerError, build_message, send_email

SETTINGS = Settings(
    _env_file=None,
    ses_usr=SecretStr("user"),
    ses_pwd=SecretStr("pwd"),
    sender_email="digest@example.org",
    digest_recipient="reader@example.org",
)


def test_build_message_is_multipart_alternative():
    msg = build_message(
        "Oggetto è", "<p>ciao</p>", "ciao", "digest@example.org", "feed-brain", "to@example.org"
    )
    assert msg["Subject"] == "Oggetto è"
    assert msg["To"] == "to@example.org"
    assert "feed-brain" in msg["From"] and "digest@example.org" in msg["From"]
    types = [part.get_content_type() for part in msg.iter_parts()]
    assert types == ["text/plain", "text/html"]


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context):  # noqa: ARG002
        self.calls.append("starttls")

    def login(self, user, pwd):
        self.calls.append(f"login:{user}:{pwd}")

    def send_message(self, msg):
        self.sent.append(msg)


async def test_send_email_uses_starttls_and_credentials(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    to = await send_email("S", "<p>h</p>", "t", settings=SETTINGS)

    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("email-smtp.us-east-1.amazonaws.com", 587)
    assert smtp.calls == ["starttls", "login:user:pwd"]
    assert smtp.sent[0]["To"] == "reader@example.org" == to


async def test_send_email_recipient_override(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    await send_email("S", "<p>h</p>", "t", to="other@example.org", settings=SETTINGS)
    assert FakeSMTP.instances[0].sent[0]["To"] == "other@example.org"


async def test_send_email_requires_credentials_and_recipient():
    with pytest.raises(MailerError, match="SES"):
        await send_email("S", "h", "t", settings=Settings(_env_file=None, digest_recipient="x@y.z"))
    with pytest.raises(MailerError, match="recipient"):
        await send_email(
            "S", "h", "t", settings=SETTINGS.model_copy(update={"digest_recipient": None})
        )


def test_build_message_has_date_and_single_line_subject():
    msg = build_message("Titolo\ncon a capo", "<p>x</p>", "x", "a@b.com", "N", "c@d.com")
    assert msg["Date"]
    assert msg["Subject"] == "Titolo con a capo"
