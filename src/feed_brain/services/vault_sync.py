# ABOUTME: Mac side of the vault bridge (`feed-brain sync-vault`, run by launchd): outbox → Obsidian vault.
# ABOUTME: Appends entries with URL dedup, writes digest notes, archives processed objects, pushes the OPML.

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import structlog

from feed_brain.services.integrator import append_entry
from feed_brain.services.outbox import DIGESTS, ENTRIES, PENDING, PROCESSED, REJECTED
from feed_brain.services.storage import Storage

log = structlog.get_logger()


@dataclass
class SyncResult:
    appended: int = 0
    duplicates: int = 0
    digests: int = 0
    rejected: int = 0
    failed: int = 0


DIGEST_NAME = re.compile(r"^feed-brain [0-9A-Za-z-]+\.md$")


def _archive(store: Storage, path: str, folder: str = PROCESSED) -> None:
    store.move(path, folder + path.removeprefix(PENDING))


def sync_vault(store: Storage, vault: Path) -> SyncResult:
    """Apply pending outbox objects to the vault.

    Applied objects move to processed/, malformed ones to rejected/; objects that hit a
    write error stay in pending/ and are retried on the next run.
    """
    result = SyncResult()
    for path in store.list(ENTRIES):
        text = store.read_text(path)
        if text is None:
            continue
        try:
            payload = json.loads(text)
            appended = append_entry(
                vault, payload["topic"], payload["url"], payload["entry"], payload["timeline"]
            )
        except (ValueError, KeyError, TypeError) as e:
            log.error("vault_entry_rejected", path=path, error=str(e))
            result.rejected += 1
            _archive(store, path, REJECTED)
            continue
        except OSError as e:
            log.error("vault_write_failed", path=path, error=str(e))
            result.failed += 1
            continue
        if appended:
            result.appended += 1
        else:
            result.duplicates += 1
        _archive(store, path)

    digests_dir = vault / "Digests"
    for path in store.list(DIGESTS):
        name = PurePosixPath(path).name
        if not DIGEST_NAME.match(name):
            log.error("vault_digest_rejected", path=path)
            result.rejected += 1
            _archive(store, path, REJECTED)
            continue
        text = store.read_text(path)
        if text is None:
            continue
        try:
            digests_dir.mkdir(parents=True, exist_ok=True)
            (digests_dir / name).write_text(text, encoding="utf-8")
        except OSError as e:
            log.error("vault_write_failed", path=path, error=str(e))
            result.failed += 1
            continue
        result.digests += 1
        _archive(store, path)

    log.info("vault_synced", **result.__dict__)
    return result


def push_opml(store: Storage, local_path: Path, object_name: str) -> bool:
    """Upload the local OPML when it differs from the bucket copy; True when uploaded."""
    if not local_path.is_file():
        return False
    local = local_path.read_text(encoding="utf-8")
    if store.read_text(object_name) == local:
        return False
    store.write_text(object_name, local, "text/x-opml; charset=utf-8")
    log.info("opml_pushed", object=object_name)
    return True
