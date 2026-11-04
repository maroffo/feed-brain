# ABOUTME: Tests for the vault outbox (cloud side) and sync-vault (Mac side), end to end on local storage.
# ABOUTME: Entries are appended once (URL dedup), digests land in Digests/, objects move to processed/.

import json
from datetime import UTC, datetime

from sqlalchemy import select

from feed_brain.config import Settings
from feed_brain.db.models import Article, Bulletin
from feed_brain.services.outbox import DIGESTS, ENTRIES, queue_digest, queue_entries
from feed_brain.services.storage import LocalStorage
from feed_brain.services.vault_sync import push_opml, sync_vault

SETTINGS = Settings(_env_file=None, auto_threshold=0.8)
# every seeded article is a lead except "not-lead", so the tier/confidence/once rules do the filtering
LEADS = {f"https://x/{t}" for t in ("keep", "lowconf", "medium", "done", "other-bulletin")}


async def _seed(session_factory) -> int:
    async with session_factory() as session:
        bulletin = Bulletin(
            issue_date=datetime.now(UTC).date(),
            title="T",
            subtitle="",
            editorial="",
            content_json="{}",
            articles_count=3,
        )
        session.add(bulletin)
        await session.flush()

        def art(title, **kw):
            data = {
                "url": f"https://x/{title}",
                "title": title,
                "status": "triaged",
                "tier": "high",
                "confidence": 0.9,
                "category": "devops_cloud",
                "summary": "Sintesi. Altro.",
                "reason": "Perché sì.",
                "bulletin_id": bulletin.id,
            }
            data.update(kw)
            return Article(**data)

        session.add_all(
            [
                art("keep"),
                art("lowconf", confidence=0.5),
                art("medium", tier="medium"),
                art("done", integrated_at=datetime.now(UTC)),
                art("other-bulletin", bulletin_id=None),
                art("not-lead"),
            ]
        )
        await session.commit()
        return bulletin.id


async def test_queue_entries_applies_integration_rule_and_records(session_factory, tmp_path):
    bulletin_id = await _seed(session_factory)
    store = LocalStorage(tmp_path)

    queued = await queue_entries(store, bulletin_id, LEADS, SETTINGS, record=True)

    assert queued == 1
    [path] = store.list(ENTRIES)
    payload = json.loads(store.read_text(path))
    assert payload["url"] == "https://x/keep"
    assert payload["topic"] == "Second Brain - DevOps and Cloud"
    assert "### keep" in payload["entry"] and "[Source](https://x/keep)" in payload["entry"]
    async with session_factory() as session:
        keep = (await session.execute(select(Article).where(Article.title == "keep"))).scalar_one()
    assert keep.status == "integrated" and keep.integration_target == payload["topic"]
    # second run: nothing new
    assert await queue_entries(store, bulletin_id, LEADS, SETTINGS, record=True) == 0


async def test_queue_entries_dry_run_does_not_record(session_factory, tmp_path):
    bulletin_id = await _seed(session_factory)
    assert (
        await queue_entries(LocalStorage(tmp_path), bulletin_id, LEADS, SETTINGS, record=False) == 1
    )
    assert (
        await queue_entries(LocalStorage(tmp_path), bulletin_id, LEADS, SETTINGS, record=False) == 1
    )


def test_sync_vault_appends_once_writes_digests_and_moves(tmp_path):
    outbox = LocalStorage(tmp_path / "outbox")
    vault = tmp_path / "vault"
    entry = {
        "article_id": 1,
        "url": "https://x/1",
        "title": "One",
        "topic": "Second Brain - DevOps and Cloud",
        "entry": "### One\n- [Source](https://x/1)\n",
        "timeline": "- 2026-11-02 | [One]",
    }
    outbox.write_text(f"{ENTRIES}1.json", json.dumps(entry), "application/json")
    outbox.write_text(
        f"{ENTRIES}2.json", json.dumps({**entry, "article_id": 2}), "application/json"
    )
    queue_digest(outbox, "feed-brain 2026-11-02", "# Bollettino\n")

    result = sync_vault(outbox, vault)

    assert (result.appended, result.duplicates, result.digests) == (1, 1, 1)
    assert (vault / "Second Brain - DevOps and Cloud.md").read_text().count("### One") == 1
    assert (vault / "Digests" / "feed-brain 2026-11-02.md").read_text() == "# Bollettino\n"
    assert outbox.list("vault-outbox/pending/") == []
    assert len(outbox.list("vault-outbox/processed/")) == 3
    assert sync_vault(outbox, vault).appended == 0


def test_push_opml_uploads_only_when_changed(tmp_path):
    store = LocalStorage(tmp_path / "bucket")
    opml = tmp_path / "Feeds.opml"
    assert push_opml(store, opml, "opml/Feeds.opml") is False  # no local file
    opml.write_text("<opml/>")
    assert push_opml(store, opml, "opml/Feeds.opml") is True
    assert push_opml(store, opml, "opml/Feeds.opml") is False
    assert store.read_text("opml/Feeds.opml") == "<opml/>"
    assert DIGESTS.startswith("vault-outbox/pending/")


async def test_queue_entries_without_leads_queues_nothing(session_factory, tmp_path):
    bulletin_id = await _seed(session_factory)
    assert (
        await queue_entries(LocalStorage(tmp_path), bulletin_id, set(), SETTINGS, record=True) == 0
    )


def test_sync_vault_rejects_bad_payloads_and_names(tmp_path):
    outbox = LocalStorage(tmp_path / "outbox")
    vault = tmp_path / "vault"
    evil = {"url": "https://x/1", "topic": "../../escape", "entry": "e", "timeline": "t"}
    outbox.write_text(f"{ENTRIES}1.json", json.dumps(evil), "application/json")
    outbox.write_text(f"{ENTRIES}2.json", "not json", "application/json")
    outbox.write_text(f"{DIGESTS}..hidden.md", "x", "text/markdown")

    result = sync_vault(outbox, vault)

    assert result.rejected == 3
    assert not (tmp_path / "escape.md").exists()
    assert len(outbox.list("vault-outbox/rejected/")) == 3
    assert outbox.list("vault-outbox/pending/") == []


def test_sync_vault_write_failure_keeps_object_pending(tmp_path):
    outbox = LocalStorage(tmp_path / "outbox")
    vault = tmp_path / "vault"
    vault.write_text("a file where the vault directory should be")
    entry = {
        "url": "https://x/1",
        "topic": "Second Brain - DevOps and Cloud",
        "entry": "### One\n- [Source](https://x/1)\n",
        "timeline": "- t",
    }
    outbox.write_text(f"{ENTRIES}1.json", json.dumps(entry), "application/json")

    result = sync_vault(outbox, vault)

    assert (result.failed, result.duplicates, result.appended) == (1, 0, 0)
    assert outbox.list(ENTRIES) == [f"{ENTRIES}1.json"]


def test_every_edition_note_reaches_the_vault_and_odd_names_still_do_not(tmp_path):
    from datetime import date

    from feed_brain.models import EDITIONS
    from feed_brain.services.outbox import queue_digest

    outbox = LocalStorage(tmp_path / "outbox")
    vault = tmp_path / "vault"
    names = [edition.note_name(date(2026, 11, 4)) for edition in EDITIONS.values()]
    for name in names:
        queue_digest(outbox, name, "# x\n")
    for odd in (
        "feed-brain 2026-11-04  doppio",
        "feed-brain 2026-11-04 ../x",
        "feed-brain 2026-11-04 ",
    ):
        outbox.write_text(f"{DIGESTS}{odd}.md", "x", "text/markdown")

    result = sync_vault(outbox, vault)

    assert result.digests == len(EDITIONS) and result.rejected == 3
    assert {p.stem for p in (vault / "Digests").iterdir()} == set(names)
