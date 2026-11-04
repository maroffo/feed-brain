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


def test_serve_runs_the_vote_service_on_the_cloud_run_port(monkeypatch):
    """`serve` hands the vote app to uvicorn on $PORT, without access logs (links carry signatures)."""
    import uvicorn
    from pydantic import SecretStr

    from feed_brain import __main__ as cli
    from feed_brain.config import Settings

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setattr(
        "feed_brain.config.get_settings",
        lambda: Settings(_env_file=None, feedback_key=SecretStr("k" * 32)),
    )
    monkeypatch.setenv("PORT", "9123")
    monkeypatch.setattr("sys.argv", ["feed-brain", "serve"])
    cli.main()
    assert seen["port"] == 9123 and seen["host"] == "0.0.0.0" and seen["access_log"] is False
    assert seen["app"].__class__.__name__ == "Starlette"


def test_bulletin_builds_the_edition_it_is_asked_for(monkeypatch, tmp_path):
    """`bulletin --edition engineering` generates that edition; unknown editions are refused."""
    from datetime import date

    import pytest

    from feed_brain import __main__ as cli
    from feed_brain.services import bulletin as bulletin_mod

    seen = {}

    async def fake_generate(issue_date, *, edition, force=False, anthropic=None):  # noqa: ARG001
        seen.update(issue_date=issue_date, edition=edition.key, force=force)

    monkeypatch.setattr(bulletin_mod, "generate_bulletin", fake_generate)
    monkeypatch.setattr("feed_brain.services.analyzer.make_client", lambda: None)
    argv = ["feed-brain", "bulletin", "--edition", "engineering", "--date", "2026-11-05"]
    monkeypatch.setattr("sys.argv", [*argv, "--out", str(tmp_path)])
    cli.main()
    assert seen == {"issue_date": date(2026, 11, 5), "edition": "engineering", "force": False}

    monkeypatch.setattr("sys.argv", ["feed-brain", "bulletin", "--edition", "nope"])
    with pytest.raises(SystemExit):
        cli.main()
