# ABOUTME: Tests for CLI command bodies that touch the database.
# ABOUTME: Run against the in-memory SQLite session factory fixture.

from sqlalchemy import select

from feed_brain.__main__ import _run_reset
from feed_brain.db.models import Article


async def test_reset_clears_triage_fields_including_section(session_factory):
    """`reset` puts triaged articles back to new and clears tier, section and category."""
    async with session_factory() as session:
        session.add(
            Article(
                url="https://x.example/1",
                title="T",
                status="triaged",
                tier="high",
                section="security",
                category="devops_cloud",
                confidence=0.9,
            )
        )
        await session.commit()

    await _run_reset()

    async with session_factory() as session:
        article = (await session.execute(select(Article))).scalar_one()
    assert article.status == "new"
    assert (article.tier, article.section, article.category) == (None, None, None)


async def test_reset_clears_bulletin_stamp(session_factory):
    from datetime import date

    from feed_brain.db.models import Bulletin

    async with session_factory() as session:
        bulletin = Bulletin(
            issue_date=date(2026, 11, 2),
            title="T",
            subtitle="",
            editorial="",
            content_json="{}",
            articles_count=1,
        )
        session.add(bulletin)
        await session.flush()
        session.add(
            Article(url="https://x/2", title="S", status="triaged", bulletin_id=bulletin.id)
        )
        await session.commit()

    await _run_reset()

    async with session_factory() as session:
        article = (await session.execute(select(Article))).scalar_one()
    assert article.bulletin_id is None


def test_cloud_severity_renames_level():
    from feed_brain.__main__ import _cloud_severity

    assert _cloud_severity(None, "info", {"level": "warning", "event": "x"}) == {
        "event": "x",
        "severity": "WARNING",
    }


def test_collect_dry_run_flag_reaches_the_pipeline(monkeypatch):
    """`collect --dry-run` reads the newsletter inbox without deleting from it."""
    from feed_brain import __main__ as cli

    seen = {}

    async def fake_run_collect(dry_run=False):
        seen["dry_run"] = dry_run

    monkeypatch.setattr(cli, "_run_collect", fake_run_collect)
    monkeypatch.setattr("sys.argv", ["feed-brain", "collect", "--dry-run"])
    cli.main()
    assert seen == {"dry_run": True}
