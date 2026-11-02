# ABOUTME: Scheduled daily run: collect (RSS + newsletter inbox) → bulletin → deliver → weekly if due.
# ABOUTME: Steps are isolated (one failure does not skip the rest); returns failed step names for the exit code.

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import structlog

from feed_brain.config import Settings
from feed_brain.db.session import get_session_factory
from feed_brain.services.analyzer import make_client
from feed_brain.services.bulletin import generate_bulletin
from feed_brain.services.delivery import deliver_bulletin, deliver_weekly
from feed_brain.services.fetcher import fetch_all_feeds
from feed_brain.services.inbox import S3Mailbox, ingest_inbox
from feed_brain.services.opml import parse_opml, sync_feed_sources
from feed_brain.services.storage import GcsStorage, LocalStorage, Storage
from feed_brain.services.triage import triage_new_articles
from feed_brain.services.weekly import due_week, existing_week_starts, generate_weekly, week_start

log = structlog.get_logger()


def stores(settings: Settings, dry_run: bool) -> tuple[Storage, Storage, Storage | None]:
    """(site, outbox, opml source). Dry runs write to ./site and ./outbox and skip the OPML."""
    if dry_run:
        return LocalStorage(Path("site")), LocalStorage(Path("outbox")), None
    if not settings.site_bucket or not settings.data_bucket:
        raise ValueError("SITE_BUCKET and DATA_BUCKET are required outside --dry-run")
    data = GcsStorage.from_name(settings.data_bucket)
    return GcsStorage.from_name(settings.site_bucket), data, data


async def collect(opml_store: Storage | None, settings: Settings, *, dry_run: bool = False) -> None:
    """Refresh feeds from the bucket OPML (if any), fetch, read the newsletter inbox, triage.

    An inbox failure does not hold back RSS triage; it is re-raised afterwards so the step
    still fails. Dry runs read the inbox without deleting from it.
    """
    if opml_store is not None:
        text = opml_store.read_text(settings.opml_object)
        if text:
            feeds = parse_opml(text)
            async with get_session_factory()() as session:
                stats = await sync_feed_sources(session, feeds, deactivate_missing=True)
                await session.commit()
            log.info("opml_synced", feeds=len(feeds), **stats.__dict__)
        else:
            log.warning("opml_missing", object=settings.opml_object)
    new = await fetch_all_feeds()
    inbox_error: Exception | None = None
    if settings.inbox_bucket:
        try:
            mailbox = await asyncio.to_thread(S3Mailbox.from_settings, settings)
            await ingest_inbox(mailbox, settings, delete=not dry_run)
        except Exception as e:  # noqa: BLE001 - re-raised after triage
            log.exception("inbox_failed", error=str(e))
            inbox_error = e
    triaged = await triage_new_articles()
    log.info("collect_done", new_articles=new, triaged=triaged)
    if inbox_error is not None:
        raise inbox_error


async def run_daily(*, today: date, dry_run: bool, to: str | None, settings: Settings) -> list[str]:
    """Run the whole day; returns the names of the steps that failed (empty = success)."""
    errors: list[str] = []

    async def step(name: str, action: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await action()
        except Exception as e:  # noqa: BLE001 - isolate steps, report at the end
            log.exception("step_failed", step=name, error=str(e))
            errors.append(name)
            return None

    try:
        site, outbox, opml_store = stores(settings, dry_run)
    except ValueError as e:
        log.error("run_misconfigured", error=str(e))
        return ["config"]

    send, record = not dry_run, not dry_run
    await step("collect", lambda: collect(opml_store, settings, dry_run=dry_run))

    bulletin = await step("bulletin", lambda: generate_bulletin(today, anthropic=make_client()))
    if bulletin is not None:
        await step(
            "deliver_bulletin",
            lambda: deliver_bulletin(
                bulletin.id,
                site=site,
                outbox=outbox,
                send=send,
                record=record,
                to=to,
                settings=settings,
            ),
        )

    monday = week_start(today)
    existing = await step(
        "weekly_check", lambda: existing_week_starts([monday, monday - timedelta(days=7)])
    )
    due = due_week(today, existing) if existing is not None else None
    if due is not None:
        digest = await step("weekly", lambda: generate_weekly(due))
        if digest is not None:
            await step(
                "deliver_weekly",
                lambda: deliver_weekly(
                    digest.id,
                    site=site,
                    outbox=outbox,
                    send=send,
                    record=record,
                    to=to,
                    settings=settings,
                ),
            )

    log.info("run_done", today=str(today), dry_run=dry_run, failed=errors)
    return errors
