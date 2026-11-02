# ABOUTME: Sends the bulletin and recap through AWS SES SMTP (STARTTLS on 587), reusing BBP's credentials.
# ABOUTME: Multipart text (Markdown note) + HTML (rendered page); smtplib runs in a worker thread.

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

import structlog

from feed_brain.config import Settings, get_settings

log = structlog.get_logger()


class MailerError(Exception):
    """Email cannot be sent with the current configuration."""


def build_message(
    subject: str, html: str, text: str, sender: str, sender_name: str, to: str
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = " ".join(subject.split())
    msg["Date"] = formatdate(localtime=True)
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def _send(host: str, port: int, user: str, password: str, msg: EmailMessage) -> None:
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)


async def send_email(
    subject: str,
    html: str,
    text: str,
    *,
    to: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Send one message; returns the recipient. Raises MailerError on missing config."""
    settings = settings or get_settings()
    if settings.ses_usr is None or settings.ses_pwd is None:
        raise MailerError("SES credentials (SES_USR / SES_PWD) are not set")
    recipient = to or settings.digest_recipient
    if not recipient:
        raise MailerError("no recipient: set DIGEST_RECIPIENT or pass --to")
    msg = build_message(subject, html, text, settings.sender_email, settings.sender_name, recipient)
    await asyncio.to_thread(
        _send,
        settings.smtp_host,
        settings.smtp_port,
        settings.ses_usr.get_secret_value(),
        settings.ses_pwd.get_secret_value(),
        msg,
    )
    # Gmail clips HTML bodies over ~102 KB: the size is logged to watch that margin.
    log.info("email_sent", subject=subject[:80], html_bytes=len(html.encode()))
    return recipient
