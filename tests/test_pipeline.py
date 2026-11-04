# ABOUTME: Tests for the scheduled daily pipeline: step isolation, dry-run wiring, weekly when due.
# ABOUTME: Every step is replaced by a recording fake; no DB, network or LLM involved.

from datetime import date
from types import SimpleNamespace

import pytest

from feed_brain.config import Settings
from feed_brain.services import pipeline
from feed_brain.services.storage import LocalStorage


@pytest.fixture
def calls(monkeypatch):
    log: list[tuple] = []

    async def collect(opml_store, settings, *, dry_run=False):  # noqa: ARG001
        log.append(("collect", opml_store, dry_run))

    issue_ids = {"daily": 7, "engineering": 8, "ai_dev": None, "research": 9, "threat_radar": 10}

    async def generate_bulletin(issue_date, *, edition, anthropic=None):  # noqa: ARG001
        log.append(("bulletin", issue_date, edition.key))
        issue_id = issue_ids[edition.key]  # None: an edition with nothing to say
        return None if issue_id is None else SimpleNamespace(id=issue_id)

    async def deliver_bulletin(bulletin_id, *, site, outbox, send, record, to, settings):  # noqa: ARG001
        log.append(("deliver_bulletin", bulletin_id, type(site).__name__, send, record, to))

    async def existing_week_starts(weeks):  # noqa: ARG001
        return set()

    async def generate_weekly(start):
        log.append(("weekly", start))
        return SimpleNamespace(id=3)

    async def deliver_weekly(digest_id, *, site, outbox, send, record, to, settings):  # noqa: ARG001
        log.append(("deliver_weekly", digest_id, send))

    for name, fn in {
        "collect": collect,
        "generate_bulletin": generate_bulletin,
        "deliver_bulletin": deliver_bulletin,
        "existing_week_starts": existing_week_starts,
        "generate_weekly": generate_weekly,
        "deliver_weekly": deliver_weekly,
    }.items():
        monkeypatch.setattr(pipeline, name, fn)
    monkeypatch.setattr(pipeline, "make_client", lambda: None)
    return log


async def test_dry_run_on_sunday_runs_everything_locally(calls, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    errors = await pipeline.run_daily(
        today=date(2026, 11, 1), dry_run=True, to=None, settings=Settings(_env_file=None)
    )
    assert errors == []
    day = date(2026, 11, 1)
    assert calls == [
        ("collect", None, True),
        ("bulletin", day, "daily"),
        ("deliver_bulletin", 7, "LocalStorage", False, False, None),
        ("bulletin", day, "engineering"),
        ("deliver_bulletin", 8, "LocalStorage", False, False, None),
        ("bulletin", day, "ai_dev"),  # empty: nothing delivered
        ("bulletin", day, "research"),
        ("deliver_bulletin", 9, "LocalStorage", False, False, None),
        ("bulletin", day, "threat_radar"),
        ("deliver_bulletin", 10, "LocalStorage", False, False, None),
        ("weekly", date(2026, 10, 26)),
        ("deliver_weekly", 3, False),
    ]


async def test_failing_step_does_not_stop_the_rest(calls, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    async def boom(opml_store, settings, *, dry_run=False):  # noqa: ARG001
        raise RuntimeError("feeds down")

    monkeypatch.setattr(pipeline, "collect", boom)
    errors = await pipeline.run_daily(
        today=date(2026, 11, 3), dry_run=True, to="me@x.y", settings=Settings(_env_file=None)
    )
    assert errors == ["collect"]
    assert ("deliver_bulletin", 7, "LocalStorage", False, False, "me@x.y") in calls
    assert ("weekly", date(2026, 10, 26)) in calls  # Tuesday catch-up of last week


async def test_real_run_requires_buckets(calls):
    errors = await pipeline.run_daily(
        today=date(2026, 11, 3), dry_run=False, to=None, settings=Settings(_env_file=None)
    )
    assert errors == ["config"]
    assert calls == []


def test_stores_for_dry_run_are_local(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    site, outbox, opml = pipeline.stores(Settings(_env_file=None), dry_run=True)
    assert isinstance(site, LocalStorage) and isinstance(outbox, LocalStorage) and opml is None


@pytest.fixture
def collect_steps(monkeypatch):
    """Fakes for the pieces collect() chains; returns the call log."""
    log: list[tuple] = []

    async def fetch_all_feeds():
        log.append(("fetch",))
        return 0

    async def triage_new_articles():
        log.append(("triage",))
        return 0

    async def ingest_inbox(mailbox, settings, *, delete=True):  # noqa: ARG001
        log.append(("inbox", mailbox, delete))

    monkeypatch.setattr(pipeline, "fetch_all_feeds", fetch_all_feeds)
    monkeypatch.setattr(pipeline, "triage_new_articles", triage_new_articles)
    monkeypatch.setattr(pipeline, "ingest_inbox", ingest_inbox)
    monkeypatch.setattr(
        pipeline.S3Mailbox, "from_settings", classmethod(lambda _cls, _settings: "box")
    )
    return log


async def test_collect_skips_the_inbox_when_no_bucket_is_configured(collect_steps):
    await pipeline.collect(None, Settings(_env_file=None))
    assert collect_steps == [("fetch",), ("triage",)]


@pytest.mark.parametrize(("dry_run", "delete"), [(False, True), (True, False)])
async def test_collect_ingests_the_inbox_before_triage(collect_steps, dry_run, delete):
    await pipeline.collect(None, Settings(_env_file=None, inbox_bucket="b"), dry_run=dry_run)
    assert collect_steps == [("fetch",), ("inbox", "box", delete), ("triage",)]


async def test_inbox_failure_still_triages_then_fails_the_step(collect_steps, monkeypatch):
    async def broken(mailbox, settings, *, delete=True):  # noqa: ARG001
        raise RuntimeError("S3 down")

    monkeypatch.setattr(pipeline, "ingest_inbox", broken)
    with pytest.raises(RuntimeError, match="S3 down"):
        await pipeline.collect(None, Settings(_env_file=None, inbox_bucket="b"))
    assert collect_steps == [("fetch",), ("triage",)]


async def test_a_failing_edition_does_not_stop_the_others(calls, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    real = pipeline.generate_bulletin

    async def flaky(issue_date, *, edition, anthropic=None):
        if edition.key == "engineering":
            raise RuntimeError("gemini down")
        return await real(issue_date, edition=edition, anthropic=anthropic)

    monkeypatch.setattr(pipeline, "generate_bulletin", flaky)
    errors = await pipeline.run_daily(
        today=date(2026, 11, 3), dry_run=True, to=None, settings=Settings(_env_file=None)
    )
    assert errors == ["bulletin:engineering"]
    delivered = [c[1] for c in calls if c[0] == "deliver_bulletin"]
    assert delivered == [7, 9, 10]
