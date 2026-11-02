# ABOUTME: Tests for parsing forwarded newsletters (SES raw MIME) into inbox articles.
# ABOUTME: Synthetic mails mirror real Substack and Drip structures; tokens must never survive.

import base64
import json
from datetime import UTC
from email.message import EmailMessage
from urllib.parse import urlsplit

import pytest

from feed_brain.services.inbox import (
    SkipMail,
    decode_redirect,
    parse_mail,
    post_url,
    strip_forward_prefix,
)

SECRET = "SECRETTOKEN123"
BODY = (
    "Buongiorno! Oggi parliamo di Stato di diritto in Europa, delle riforme della giustizia "
    "e di cosa cambia per le istituzioni dell'Unione nei prossimi mesi. "
) * 12


def b64(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def substack_redirect(dest: str) -> str:
    return f"https://substack.com/redirect/2/{b64({'e': dest, 'p': 1, 'u': 2})}.sig?j=x"


def drip_link(dest: str) -> str:
    header = b64({"alg": "HS256"})
    claims = b64({"aud": "detour", "url": dest, "token": SECRET})
    return f"https://t.dripemail2.com/c/{header}.{claims}.signature"


def make_mail(
    *,
    html: str,
    subject: str = "Fwd: Polonia e Stato di diritto",
    sender: str = "Max <max@example.com>",
    plain: str = "see html",
    extra: dict[str, str] | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "newsletter@in.example.com"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 02 Nov 2026 20:17:04 +0100"
    msg["Message-ID"] = "<CAF123@mail.gmail.com>"
    msg["X-SES-Spam-Verdict"] = "PASS"
    msg["X-SES-Virus-Verdict"] = "PASS"
    for name, value in (extra or {}).items():
        del msg[name]
        msg[name] = value
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


def substack_html(post: str) -> str:
    """Shaped like real Substack mails: a redirect to the post, app links, and the post's own
    open.substack.com share/comment links (all carrying personal tokens)."""
    parts = urlsplit(post)
    pub = parts.netloc.split(".")[0]
    slug = parts.path.rsplit("/", 1)[-1]
    open_link = f"https://open.substack.com/pub/{pub}/p/{slug}?r=w4ca&token={SECRET}&action=share"
    return (
        '<div class="gmail_quote">'
        f'<a href="{substack_redirect("https://pub.substack.com/subscribe?r=w4ca")}">Iscriviti</a>'
        f'<a href="{substack_redirect(post)}">Visualizza nel browser</a>'
        f'<a href="https://substack.com/app-link/post?publication_id=1&post_id=2&token={SECRET}">'
        "Polonia e Stato di diritto</a>"
        f"<article><h1>Polonia e Stato di diritto</h1><p>{BODY}</p></article>"
        f'<a href="{open_link}">Condividi</a>'
        f'<a href="{substack_redirect(f"https://pub.substack.com/action/disable_email?token={SECRET}")}">'
        "Unsubscribe</a></div>"
    )


def test_manual_substack_forward_becomes_a_clean_article():
    post = f"https://davidcarretta.substack.com/p/polonia-il-golpe?r=w4ca&token={SECRET}&utm_campaign=x"
    mail = parse_mail(make_mail(html=substack_html(post)))

    assert mail.url == "https://davidcarretta.substack.com/p/polonia-il-golpe"
    assert mail.title == "Polonia e Stato di diritto"
    assert mail.source_name == "davidcarretta.substack.com"
    assert mail.source_key == "davidcarretta.substack.com"
    assert "Stato di diritto in Europa" in mail.content
    assert SECRET not in mail.content and SECRET not in mail.url
    assert "http" not in mail.content  # text only: no links, tracked or not
    assert mail.published is not None and mail.published.year == 2026
    assert mail.message_id == "<CAF123@mail.gmail.com>"


def test_auto_forward_keeps_the_original_sender():
    post = "https://burioni.substack.com/p/dopo-5-anni"
    mail = parse_mail(
        make_mail(
            html=substack_html(post),
            subject="Dopo 5 anni",
            sender="Roberto Burioni <burioni@substack.com>",
            extra={"X-Forwarded-For": "max@example.com newsletter@in.example.com"},
        )
    )
    assert mail.source_name == "Roberto Burioni"
    assert mail.source_key == "burioni@substack.com"
    assert mail.title == "Dopo 5 anni"
    assert mail.url == "https://burioni.substack.com/p/dopo-5-anni"


def test_digest_without_a_post_url_uses_the_message_id_and_never_stores_tracking_links():
    items = "".join(
        f'<tr><td><a href="{drip_link(f"https://thebaffler.com/salvos/item-{i}?__s={SECRET}")}">'
        f"Item {i}</a></td><td>{BODY[:200]}</td></tr>"
        for i in range(8)
    )
    html = f'<div class="gmail_quote"><table>{items}</table></div>'
    mail = parse_mail(make_mail(html=html, subject="Fwd: Your Syllabus This Week"))

    assert mail.url == "mid:CAF123@mail.gmail.com"
    assert mail.title == "Your Syllabus This Week"
    assert mail.source_name == "Newsletter"
    assert "Item 3" in mail.content
    assert SECRET not in mail.content
    assert "dripemail2" not in mail.content


@pytest.mark.parametrize("header", ["X-SES-Spam-Verdict", "X-SES-Virus-Verdict"])
def test_spam_or_virus_verdict_fail_is_dropped(header):
    raw = make_mail(html=substack_html("https://a.substack.com/p/x"), extra={header: "FAIL"})
    with pytest.raises(SkipMail) as info:
        parse_mail(raw)
    assert info.value.keep is False


def test_gmail_forwarding_confirmation_is_skipped_but_kept_for_its_code():
    raw = make_mail(
        html="<p>Confirmation code: 123456789</p>",
        subject="(#123456789) Gmail Forwarding Confirmation - Receive Mail from max@example.com",
        sender="Gmail Team <forwarding-noreply@google.com>",
    )
    with pytest.raises(SkipMail) as info:
        parse_mail(raw)
    assert info.value.keep is True


def test_mail_without_text_is_dropped():
    with pytest.raises(SkipMail) as info:
        parse_mail(make_mail(html="<div><img src='x.png'></div>", plain=" "))
    assert info.value.keep is False


def test_decode_redirect_reads_destinations_offline():
    dest = "https://example.com/p/post?token=abc"
    assert decode_redirect(substack_redirect(dest)) == dest
    assert decode_redirect(drip_link(dest)) == dest
    assert decode_redirect("https://example.com/plain") == "https://example.com/plain"
    assert decode_redirect("https://substack.com/redirect/2/not-base64!!") == (
        "https://substack.com/redirect/2/not-base64!!"
    )
    javascript = substack_redirect("javascript:alert(1)")
    assert decode_redirect(javascript) == javascript  # only http(s) destinations are trusted


def test_post_url_normalizes_open_substack_and_custom_domains():
    assert post_url([f"https://open.substack.com/pub/burioni/p/dopo?token={SECRET}"]) == (
        "https://burioni.substack.com/p/dopo"
    )
    # custom domain wins when it carries the same post as the open.substack.com link
    custom = substack_redirect(f"https://newsletter.example.org/p/the-pulse?token={SECRET}")
    opened = f"https://open.substack.com/pub/example/p/the-pulse?token={SECRET}"
    assert post_url([custom, opened]) == "https://newsletter.example.org/p/the-pulse"
    # without open.substack.com, a post link counts only when the mail repeats it
    repeated = "https://newsletter.example.org/p/slug/?utm_source=email"
    assert post_url([repeated, repeated]) == "https://newsletter.example.org/p/slug"
    assert post_url([repeated]) is None
    assert post_url(["https://substack.com/app-link/post?post_id=2", "https://x.org/about"]) is None


def test_a_post_cited_by_a_digest_is_not_the_issue_url():
    cited = drip_link("https://other.substack.com/p/cited")
    html = f'<div class="gmail_quote"><p>{BODY}</p><a href="{cited}">Cited</a><a href="{cited}">Again</a></div>'
    mail = parse_mail(make_mail(html=html, subject="Fwd: Your Syllabus This Week"))
    assert mail.url == "mid:CAF123@mail.gmail.com"
    assert mail.source_name == "Newsletter" and mail.source_key == "newsletter"


def raw_mail(headers: str, body: bytes) -> bytes:
    return headers.replace("\n", "\r\n").encode() + b"\r\n" + body


def test_mails_without_message_id_get_distinct_stable_ids():
    first = raw_mail("From: a@b.com\nSubject: Uno\n", b"Primo testo della newsletter.")
    second = raw_mail("From: a@b.com\nSubject: Due\n", b"Secondo testo della newsletter.")
    urls = {parse_mail(first).url, parse_mail(second).url}
    assert len(urls) == 2 and all(u.startswith("mid:sha256-") for u in urls)
    assert parse_mail(first).url == parse_mail(first).url


@pytest.mark.parametrize(
    ("date", "expected_utc_hour"),
    [
        ("Mon, 02 Nov 2026 20:17:04 -0000", 20),
        ("Mon, 02 Nov 2026 20:17:04 +0100", 19),
        ("not a date", None),
        (None, None),
    ],
)
def test_published_is_timezone_aware_or_none(date, expected_utc_hour):
    headers = "From: a@b.com\nSubject: x\n" + (f"Date: {date}\n" if date else "")
    mail = parse_mail(raw_mail(headers, b"Testo della newsletter."))
    if expected_utc_hour is None:
        assert mail.published is None
    else:
        assert mail.published.tzinfo is not None
        assert mail.published.astimezone(UTC).hour == expected_utc_hour


def test_unknown_charset_is_decoded_with_replacement():
    raw = raw_mail(
        "From: a@b.com\nSubject: x\nContent-Type: text/plain; charset=unknown-8bit\n",
        "Caffè e giustizia".encode(),
    )
    assert "giustizia" in parse_mail(raw).content


def test_markup_only_html_falls_back_to_the_plain_part():
    mail = parse_mail(make_mail(html="<div><img src='x.png'></div>", plain="Testo solo in chiaro."))
    assert mail.content == "Testo solo in chiaro."


def test_plain_text_only_substack_mail_keeps_its_post_url():
    link = f"https://open.substack.com/pub/burioni/p/dopo?token={SECRET}"
    raw = raw_mail(
        "From: a@b.com\nSubject: Fwd: Dopo\n", f"Leggi qui <{link}>\nTesto del post.".encode()
    )
    mail = parse_mail(raw)
    assert mail.url == "https://burioni.substack.com/p/dopo"
    assert SECRET not in mail.content


def test_a_mail_without_subject_keeps_its_sender():
    mail = parse_mail(
        make_mail(
            html=f"<p>{BODY}</p>", subject="", sender="Roberto Burioni <burioni@substack.com>"
        )
    )
    assert (mail.source_name, mail.source_key) == ("Roberto Burioni", "burioni@substack.com")
    assert mail.title == "Newsletter"


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Fwd: Titolo", "Titolo"),
        ("FW: I: Inoltro: Titolo", "Titolo"),
        ("Re: Titolo", "Re: Titolo"),
        ("Titolo: sottotitolo", "Titolo: sottotitolo"),
    ],
)
def test_strip_forward_prefix(subject, expected):
    assert strip_forward_prefix(subject) == expected
