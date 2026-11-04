# ABOUTME: Tests for delivery: publish to the site, email once, vault outbox, index page, dry runs.
# ABOUTME: Storage is local, the mailer is faked, the DB is in-memory SQLite.

from datetime import date

from pydantic import SecretStr
from sqlalchemy import select

from feed_brain.config import Settings
from feed_brain.db.models import Bulletin, WeeklyDigest
from feed_brain.models import BulletinData, BulletinRef, WeeklyData
from feed_brain.services import delivery
from feed_brain.services.delivery import deliver_bulletin, deliver_weekly
from feed_brain.services.storage import LocalStorage

SETTINGS = Settings(_env_file=None, site_url="https://site.example", digest_recipient="r@x.y")
DAY = date(2026, 11, 2)


def _bulletin_row(edition: str = "daily", title: str = "Titolo del giorno") -> Bulletin:
    data = BulletinData(
        issue_date=DAY,
        edition=edition,
        title=title,
        subtitle="",
        editorial="Il punto.",
        top_stories=[],
        sections=[],
        articles_count=0,
    )
    return Bulletin(
        issue_date=DAY,
        edition=edition,
        title=data.title,
        subtitle="",
        editorial=data.editorial,
        content_json=data.model_dump_json(),
        articles_count=0,
    )


class Sent:
    def __init__(self):
        self.messages = []
        self.html = []

    async def __call__(self, subject, html, text, *, to=None, settings=None):  # noqa: ARG002
        self.messages.append((subject, to))
        self.html.append(html)
        return to or "r@x.y"


async def test_deliver_bulletin_publishes_emails_queues_once(
    session_factory, tmp_path, monkeypatch
):
    sent = Sent()
    monkeypatch.setattr(delivery, "send_email", sent)
    async with session_factory() as session:
        row = _bulletin_row()
        session.add(row)
        await session.commit()
        bulletin_id = row.id
    site, outbox = LocalStorage(tmp_path / "site"), LocalStorage(tmp_path / "outbox")

    await deliver_bulletin(
        bulletin_id, site=site, outbox=outbox, send=True, record=True, settings=SETTINGS
    )
    await deliver_bulletin(
        bulletin_id, site=site, outbox=outbox, send=True, record=True, settings=SETTINGS
    )

    assert site.read_text("bulletins/2026-11-02.html") is not None
    index = site.read_text("index.html")
    assert 'href="bulletins/2026-11-02.html"' in index and "Titolo del giorno" in index
    assert site.read_text("robots.txt") == "User-agent: *\nDisallow: /\n"
    assert len(sent.messages) == 1
    assert sent.messages[0][0] == "Bollettino del 2 novembre 2026: Titolo del giorno"
    assert outbox.read_text("vault-outbox/pending/digests/feed-brain 2026-11-02.md").startswith(
        "---"
    )
    async with session_factory() as session:
        row = await session.get(Bulletin, bulletin_id)
    assert row.published_at is not None and row.sent_at is not None


async def test_dry_run_records_nothing_and_sends_nothing(session_factory, tmp_path, monkeypatch):
    sent = Sent()
    monkeypatch.setattr(delivery, "send_email", sent)
    async with session_factory() as session:
        row = _bulletin_row()
        session.add(row)
        await session.commit()
        bulletin_id = row.id
    site = LocalStorage(tmp_path / "site")

    await deliver_bulletin(
        bulletin_id,
        site=site,
        outbox=LocalStorage(tmp_path / "o"),
        send=False,
        record=False,
        settings=SETTINGS,
    )

    assert site.read_text("bulletins/2026-11-02.html") is not None
    assert sent.messages == []
    async with session_factory() as session:
        row = (await session.execute(select(Bulletin))).scalar_one()
    assert row.published_at is None and row.sent_at is None


async def test_deliver_weekly(session_factory, tmp_path, monkeypatch):
    sent = Sent()
    monkeypatch.setattr(delivery, "send_email", sent)
    data = WeeklyData(
        week_start=date(2026, 10, 26),
        week_end=date(2026, 11, 1),
        title="La settimana",
        subtitle="",
        intro="Intro.",
        arcs=[],
        reflection="",
        bulletins=[BulletinRef(issue_date=date(2026, 10, 26), title="L")],
        stories_count=0,
    )
    async with session_factory() as session:
        row = WeeklyDigest(
            week_start=data.week_start,
            title=data.title,
            subtitle="",
            content_json=data.model_dump_json(),
        )
        session.add(row)
        await session.commit()
        digest_id = row.id
    site, outbox = LocalStorage(tmp_path / "site"), LocalStorage(tmp_path / "outbox")

    await deliver_weekly(
        digest_id, site=site, outbox=outbox, send=True, record=True, to="me@x.y", settings=SETTINGS
    )

    assert site.read_text("weekly/2026-W44.html") is not None
    assert 'href="weekly/2026-W44.html"' in site.read_text("index.html")
    assert sent.messages == [("La settimana 2026-W44: La settimana", "me@x.y")]
    assert outbox.read_text("vault-outbox/pending/digests/feed-brain 2026-W44.md") is not None


async def test_email_failure_does_not_block_vault_notes(session_factory, tmp_path, monkeypatch):
    import pytest

    from feed_brain.services.mailer import MailerError

    async def failing(*args, **kwargs):  # noqa: ARG001
        raise MailerError("SES down")

    monkeypatch.setattr(delivery, "send_email", failing)
    async with session_factory() as session:
        row = _bulletin_row()
        session.add(row)
        await session.commit()
        bulletin_id = row.id
    outbox = LocalStorage(tmp_path / "outbox")

    with pytest.raises(MailerError):
        await deliver_bulletin(
            bulletin_id,
            site=LocalStorage(tmp_path / "site"),
            outbox=outbox,
            send=True,
            record=True,
            settings=SETTINGS,
        )

    assert outbox.read_text("vault-outbox/pending/digests/feed-brain 2026-11-02.md") is not None
    async with session_factory() as session:
        row = await session.get(Bulletin, bulletin_id)
    assert row.published_at is not None and row.sent_at is None


async def test_site_gets_the_public_variant_and_email_the_full_one(
    session_factory, tmp_path, monkeypatch
):
    from feed_brain.models import Section, SourceRef, TopStory

    sent = Sent()
    monkeypatch.setattr(delivery, "send_email", sent)
    paid = SourceRef(
        name="pub.substack.com", title="Post", url="https://pub.substack.com/p/x", paid=True
    )
    data = BulletinData(
        issue_date=DAY,
        title="Titolo del giorno",
        subtitle="",
        editorial="Il punto.",
        top_stories=[
            TopStory(
                title="Storia", text="Testo riservato.", section=Section.POLITICS, sources=[paid]
            )
        ],
        sections=[],
        articles_count=1,
    )
    async with session_factory() as session:
        row = Bulletin(
            issue_date=DAY,
            title=data.title,
            subtitle="",
            editorial=data.editorial,
            content_json=data.model_dump_json(),
            articles_count=1,
        )
        session.add(row)
        await session.commit()
        bulletin_id = row.id
    site, outbox = LocalStorage(tmp_path / "site"), LocalStorage(tmp_path / "outbox")

    await deliver_bulletin(
        bulletin_id, site=site, outbox=outbox, send=True, record=True, settings=SETTINGS
    )

    page = site.read_text("bulletins/2026-11-02.html")
    assert "Storia" in page and "Testo riservato" not in page
    assert "Testo riservato" in sent.html[0]


async def test_email_gets_vote_links_only_when_the_feedback_service_is_configured(
    session_factory, tmp_path, monkeypatch
):
    from feed_brain.models import Section, SourceRef, TopStory
    from feed_brain.services.feedback import StoryLinks

    sent = Sent()
    monkeypatch.setattr(delivery, "send_email", sent)
    data = BulletinData(
        issue_date=DAY,
        title="Titolo",
        top_stories=[
            TopStory(
                title="Storia",
                text="Testo.",
                section=Section.POLITICS,
                sources=[SourceRef(name="Il Post", title="P", url="https://ilpost/p")],
            )
        ],
        sections=[],
        articles_count=1,
    )
    configured = SETTINGS.model_copy(
        update={"feedback_url": "https://fb.run.app", "feedback_key": SecretStr("k" * 32)}
    )
    for day, settings in ((DAY, configured), (date(2026, 11, 3), SETTINGS)):
        issue = data.model_copy(update={"issue_date": day})
        async with session_factory() as session:
            row = Bulletin(
                issue_date=day,
                title="Titolo",
                subtitle="",
                editorial="",
                content_json=issue.model_dump_json(),
                articles_count=1,
            )
            session.add(row)
            await session.commit()
            bulletin_id = row.id
        site, outbox = LocalStorage(tmp_path / "site"), LocalStorage(tmp_path / "outbox")
        await deliver_bulletin(
            bulletin_id, site=site, outbox=outbox, send=True, record=True, settings=settings
        )

    links = StoryLinks("https://fb.run.app", b"k" * 32, DAY).votes("https://ilpost/p")
    assert f'href="{links["up"]}"' in sent.html[0] and f'href="{links["down"]}"' in sent.html[0]
    assert "più così" not in site.read_text("bulletins/2026-11-02.html")
    assert "più così" not in sent.html[1]


def test_no_vote_links_with_a_blank_key():
    from feed_brain.services.delivery import story_links

    data = BulletinData(issue_date=DAY, title="t", top_stories=[], sections=[], articles_count=0)
    blank = SETTINGS.model_copy(
        update={"feedback_url": "https://fb.run.app", "feedback_key": SecretStr("  ")}
    )
    assert story_links(data, blank) is None


async def test_a_thematic_issue_has_its_own_page_subject_note_and_index_line(
    session_factory, tmp_path, monkeypatch
):
    sent = Sent()
    monkeypatch.setattr(delivery, "send_email", sent)
    async with session_factory() as session:
        daily, theme = _bulletin_row(), _bulletin_row("engineering", "Kubernetes e GC")
        session.add_all([daily, theme])
        await session.commit()
        ids = daily.id, theme.id
    site, outbox = LocalStorage(tmp_path / "site"), LocalStorage(tmp_path / "outbox")

    for bulletin_id in ids:
        await deliver_bulletin(
            bulletin_id, site=site, outbox=outbox, send=True, record=True, settings=SETTINGS
        )

    page = site.read_text("bulletins/2026-11-02-engineering.html")
    assert "Ingegneria · 2 novembre 2026" in page
    assert site.read_text("bulletins/2026-11-02.html") is not None  # the daily page is untouched
    assert [m[0] for m in sent.messages] == [
        "Bollettino del 2 novembre 2026: Titolo del giorno",
        "Ingegneria del 2 novembre 2026: Kubernetes e GC",
    ]
    assert outbox.read_text("vault-outbox/pending/digests/feed-brain 2026-11-02 Ingegneria.md")
    index = site.read_text("index.html")
    assert 'href="bulletins/2026-11-02-engineering.html"' in index
    assert "Ingegneria: Kubernetes e GC" in index
