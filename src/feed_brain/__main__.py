# ABOUTME: CLI entry point for feed-brain with full pipeline commands.
# ABOUTME: Commands: migrate, collect, bulletin, weekly, run, serve, sync-vault, fetch, triage, analyze, integrate, list, reset, import-opml.

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path

import structlog
from structlog.typing import EventDict, Processor

from feed_brain.models import EDITIONS


def _cloud_severity(_logger: object, _method: str, event_dict: EventDict) -> EventDict:
    """Cloud Logging reads the level from `severity`."""
    event_dict["severity"] = str(event_dict.pop("level", "info")).upper()
    return event_dict


def _configure_logging() -> None:
    """Human-readable logs in a terminal, JSON lines (Cloud Logging) otherwise; no debug."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso" if not sys.stderr.isatty() else "%H:%M:%S"),
    ]
    if sys.stderr.isatty():
        renderers: list[Processor] = [structlog.dev.ConsoleRenderer()]
    else:
        renderers = [
            structlog.processors.format_exc_info,
            _cloud_severity,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ]
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        processors=[*shared, *renderers],
    )


_configure_logging()
log = structlog.get_logger()


async def _shutdown() -> None:
    """Clean up database connections."""
    from feed_brain.db.session import close_db

    await close_db()


def cmd_migrate(_args: argparse.Namespace) -> None:
    """Apply pending Alembic migrations (synchronous, outside the event loop)."""
    from feed_brain.db.migrate import upgrade_head

    upgrade_head()


def cmd_serve(_args: argparse.Namespace) -> None:
    """Run the public vote service behind the email's vote links (Cloud Run sets PORT)."""
    import uvicorn

    from feed_brain.config import get_settings
    from feed_brain.web import create_app

    port = int(os.environ.get("PORT", "8080"))
    # No access log: every request path carries a link signature.
    uvicorn.run(create_app(get_settings()), host="0.0.0.0", port=port, access_log=False)  # noqa: S104


def cmd_fetch(_args: argparse.Namespace) -> None:
    """Fetch new articles from all feeds."""
    asyncio.run(_run_fetch())


def cmd_reextract(args: argparse.Namespace) -> None:
    """Extract again recent articles whose stored text is short (teasers, failed pages)."""
    asyncio.run(_run_reextract(args.hours, args.min_chars))


async def _run_reextract(hours: int, min_chars: int) -> None:
    try:
        from feed_brain.services import fetcher

        refilled = await fetcher.reextract_short(hours=hours, min_chars=min_chars)
        log.info("reextract_finished", refilled=refilled)
    finally:
        await _shutdown()


async def _run_fetch() -> None:
    try:
        from feed_brain.services.fetcher import fetch_all_feeds

        new = await fetch_all_feeds()
        log.info("fetch_done", new_articles=new)
    finally:
        await _shutdown()


def cmd_triage(args: argparse.Namespace) -> None:
    """Triage new articles with Gemini (--retriage: classify recent ones again)."""
    asyncio.run(_run_triage(args.retriage))


async def _run_triage(retriage: bool) -> None:
    try:
        from feed_brain.services.triage import triage_new_articles

        triaged = await triage_new_articles(retriage=retriage)
        log.info("triage_done", triaged=triaged)
    finally:
        await _shutdown()


def cmd_analyze(args: argparse.Namespace) -> None:
    """Deep-analyze one article with Opus (the bulletin does this for top stories)."""
    asyncio.run(_run_analyze(args.article_id))


async def _run_analyze(article_id: int) -> None:
    try:
        from feed_brain.ai.gemini import Gemini, GeminiError
        from feed_brain.db.models import Article
        from feed_brain.db.session import get_session_factory
        from feed_brain.services.analyzer import (
            apply_analysis,
            deep_analyze,
            is_arxiv,
            make_client,
        )

        client = make_client()
        if client is None:
            sys.exit("ANTHROPIC_API_KEY is not set")
        async with get_session_factory()() as session:
            article = await session.get(Article, article_id)
            if article is None:
                sys.exit(f"No article with id {article_id}")
            try:
                gemini = Gemini() if is_arxiv(article) else None
            except GeminiError as e:
                sys.exit(str(e))
            analysis = await deep_analyze(article, client, gemini)
            if analysis is None:
                sys.exit("Analysis failed (see log)")
            apply_analysis(article, analysis)
            await session.commit()
        print(analysis.model_dump_json(indent=2))
    finally:
        await _shutdown()


def cmd_integrate(_args: argparse.Namespace) -> None:
    """Push high-tier articles to Second Brain."""
    asyncio.run(_run_integrate())


async def _run_integrate() -> None:
    try:
        from feed_brain.services.integrator import integrate_articles

        integrated = await integrate_articles()
        log.info("integrate_done", integrated=integrated)
    finally:
        await _shutdown()


def cmd_collect(args: argparse.Namespace) -> None:
    """Sync feeds from the bucket OPML (when DATA_BUCKET is set), fetch, read the newsletter
    inbox (when INBOX_BUCKET is set), triage. --dry-run leaves the inbox mails in place."""
    asyncio.run(_run_collect(dry_run=args.dry_run))


async def _run_collect(dry_run: bool = False) -> None:
    try:
        from feed_brain.config import get_settings
        from feed_brain.services.pipeline import collect
        from feed_brain.services.storage import GcsStorage

        settings = get_settings()
        opml_store = GcsStorage.from_name(settings.data_bucket) if settings.data_bucket else None
        await collect(opml_store, settings, dry_run=dry_run)
    finally:
        await _shutdown()


def cmd_bulletin(args: argparse.Namespace) -> None:
    """Generate (or reuse) one edition's issue (default: the main one) and render it under --out."""
    asyncio.run(_run_bulletin(args.date, args.force, Path(args.out), args.edition))


async def _run_bulletin(issue: str | None, force: bool, out: Path, edition: str) -> None:
    try:
        await _bulletin(date.fromisoformat(issue) if issue else None, force, out, edition)
    finally:
        await _shutdown()


async def _bulletin(issue_date: date | None, force: bool, out: Path, edition_key: str) -> None:
    from feed_brain.models import BulletinData
    from feed_brain.services import analyzer
    from feed_brain.services import bulletin as bulletin_service
    from feed_brain.services.render import write_bulletin_files

    issue_date = issue_date or bulletin_service.today_rome()
    bulletin = await bulletin_service.generate_bulletin(
        issue_date, edition=EDITIONS[edition_key], force=force, anthropic=analyzer.make_client()
    )
    if bulletin is None:
        log.warning(
            "bulletin_skipped",
            issue_date=str(issue_date),
            edition=edition_key,
            reason="no candidates",
        )
        return
    data = BulletinData.model_validate_json(bulletin.content_json)
    html_path, md_path = write_bulletin_files(data, out)
    log.info("bulletin_rendered", html=str(html_path), md=str(md_path))


def cmd_weekly(args: argparse.Namespace) -> None:
    """Generate the weekly recap (--week, or whichever week is due) and render it under --out."""
    asyncio.run(_run_weekly(args.week, args.force, Path(args.out)))


async def _run_weekly(week: str | None, force: bool, out: Path) -> None:
    try:
        await _weekly(week, force, out)
    finally:
        await _shutdown()


async def _weekly(week: str | None, force: bool, out: Path) -> None:
    from datetime import timedelta

    from feed_brain.models import WeeklyData
    from feed_brain.services.bulletin import today_rome
    from feed_brain.services.render import write_weekly_files
    from feed_brain.services.weekly import (
        due_week,
        existing_week_starts,
        generate_weekly,
        parse_iso_week,
        week_start,
    )

    if week:
        start = parse_iso_week(week)
    else:
        today = today_rome()
        this_monday = week_start(today)
        existing = await existing_week_starts([this_monday, this_monday - timedelta(days=7)])
        due = due_week(today, existing)
        if due is None:
            log.info("weekly_not_due", today=str(today))
            return
        start = due
    digest = await generate_weekly(start, force=force)
    if digest is None:
        return
    html_path, md_path = write_weekly_files(
        WeeklyData.model_validate_json(digest.content_json), out
    )
    log.info("weekly_rendered", html=str(html_path), md=str(md_path))


def cmd_run(args: argparse.Namespace) -> None:
    """Scheduled entry point: migrate, collect, each edition (generate, deliver), weekly if due."""
    from feed_brain.db.migrate import upgrade_head

    upgrade_head()
    errors = asyncio.run(_run_daily(args.dry_run, args.to))
    if errors:
        sys.exit(f"failed steps: {', '.join(errors)}")


async def _run_daily(dry_run: bool, to: str | None) -> list[str]:
    try:
        from feed_brain.config import get_settings
        from feed_brain.services.bulletin import today_rome
        from feed_brain.services.pipeline import run_daily

        return await run_daily(today=today_rome(), dry_run=dry_run, to=to, settings=get_settings())
    finally:
        await _shutdown()


def cmd_sync_vault(args: argparse.Namespace) -> None:
    """Mac side: pull the vault outbox into the Obsidian vault and push the local OPML."""
    from feed_brain.config import get_settings
    from feed_brain.services.storage import GcsStorage, LocalStorage
    from feed_brain.services.vault_sync import push_opml, sync_vault

    settings = get_settings()
    vault = Path(args.vault) if args.vault else settings.vault_path
    if args.outbox:
        store = LocalStorage(Path(args.outbox))
    elif settings.data_bucket:
        store = GcsStorage.from_name(settings.data_bucket)
    else:
        sys.exit("Set DATA_BUCKET or pass --outbox DIR")
    result = sync_vault(store, vault)
    pushed = False
    if not args.outbox and not args.no_opml:
        pushed = push_opml(store, settings.local_opml_path, settings.opml_object)
    print(
        f"Entries appended: {result.appended}, duplicates skipped: {result.duplicates}, "
        f"notes written: {result.digests}, OPML uploaded: {'yes' if pushed else 'no'}"
    )


def cmd_list(args: argparse.Namespace) -> None:
    """List recent articles."""
    asyncio.run(_run_list(args.tier, args.limit))


async def _run_list(tier: str | None, limit: int) -> None:
    from sqlalchemy import select

    try:
        from feed_brain.db.models import Article
        from feed_brain.db.session import get_session_factory

        session_factory = get_session_factory()
        async with session_factory() as session:
            query = select(Article).order_by(Article.fetched_at.desc())
            if tier:
                query = query.where(Article.tier == tier)
            query = query.limit(limit)

            result = await session.execute(query)
            articles = result.scalars().all()

            if not articles:
                print("No articles found.")
                return

            for article in articles:
                tier_str = (article.tier or "?").upper().ljust(6)
                cat_str = (article.category or "?").ljust(25)
                conf_str = f"{article.confidence:.2f}" if article.confidence else "?.??"
                status = (article.status or "?").ljust(10)
                print(f"  [{tier_str}] {conf_str}  {status}  {cat_str}  {article.title[:80]}")
    finally:
        await _shutdown()


def cmd_reset(_args: argparse.Namespace) -> None:
    """Reset triaged/integrated articles back to 'new' for re-processing."""
    asyncio.run(_run_reset())


async def _run_reset() -> None:
    from typing import cast

    from sqlalchemy import CursorResult, update

    try:
        from feed_brain.db.models import Article
        from feed_brain.db.session import get_session_factory
        from feed_brain.models import ArticleStatus

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                update(Article)
                .where(
                    Article.status.in_(
                        [
                            ArticleStatus.TRIAGED,
                            ArticleStatus.ANALYZED,
                            ArticleStatus.INTEGRATED,
                        ]
                    )
                )
                .values(
                    status=ArticleStatus.NEW,
                    tier=None,
                    section=None,
                    category=None,
                    confidence=None,
                    summary=None,
                    reason=None,
                    classified_at=None,
                    integrated_at=None,
                    integration_target=None,
                    bulletin_id=None,
                    deep_summary=None,
                    deep_insights=None,
                    money_quote=None,
                    actionables=None,
                )
            )
            await session.commit()
            reset_count = cast(CursorResult, result).rowcount
            log.info("reset_done", articles_reset=reset_count)
            print(f"Reset {reset_count} articles to 'new' status.")
    finally:
        await _shutdown()


def cmd_import_opml(args: argparse.Namespace) -> None:
    """Import feeds from an OPML file."""
    asyncio.run(_run_import_opml(args.file, sync=args.sync))


async def _run_import_opml(filepath: str, sync: bool = False) -> None:
    try:
        from feed_brain.db.session import get_session_factory
        from feed_brain.services.opml import parse_opml, sync_feed_sources

        feeds = parse_opml(Path(filepath).read_text(encoding="utf-8"))
        async with get_session_factory()() as session:
            stats = await sync_feed_sources(session, feeds, deactivate_missing=sync)
            await session.commit()

        print(f"Imported {stats.imported} new feeds (of {len(feeds)} total in OPML).")
        if stats.reactivated:
            print(f"Reactivated {stats.reactivated} feeds.")
        if stats.deactivated:
            print(f"Deactivated {stats.deactivated} feeds not in OPML.")
        log.info(
            "opml_import_done",
            imported=stats.imported,
            reactivated=stats.reactivated,
            deactivated=stats.deactivated,
            total=len(feeds),
        )
    finally:
        await _shutdown()


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(prog="feed-brain", description="AI-powered feed aggregator")
    subparsers = parser.add_subparsers(dest="command")

    # migrate
    subparsers.add_parser("migrate", help="Apply pending database migrations")

    # serve
    subparsers.add_parser("serve", help="Run the vote service behind the email's vote links")

    # fetch
    subparsers.add_parser("fetch", help="Fetch new articles from all feeds")

    reextract_parser = subparsers.add_parser(
        "reextract", help="Extract again recent articles whose stored text is short"
    )
    reextract_parser.add_argument("--hours", type=int, default=48, help="Window (default 48)")
    reextract_parser.add_argument(
        "--min-chars", type=int, default=1500, help="Shorter text is extracted again (default 1500)"
    )

    # triage
    triage_parser = subparsers.add_parser("triage", help="Triage new articles with Gemini")
    triage_parser.add_argument(
        "--retriage",
        action="store_true",
        help="Classify again the triaged articles of the last ARTICLE_MAX_AGE_HOURS "
        "(after a profile change); statuses and bulletin stamps stay",
    )

    # analyze
    analyze_parser = subparsers.add_parser("analyze", help="Deep-analyze one article with Opus")
    analyze_parser.add_argument("--article-id", type=int, required=True, help="Article id")

    # integrate
    subparsers.add_parser("integrate", help="Push high-tier articles to Second Brain")

    # collect
    collect_parser = subparsers.add_parser(
        "collect", help="Fetch feeds, read the newsletter inbox, triage new articles"
    )
    collect_parser.add_argument(
        "--dry-run", action="store_true", help="Read the newsletter inbox without deleting mails"
    )

    # bulletin
    bulletin_parser = subparsers.add_parser(
        "bulletin", help="Generate and render one edition's issue (default: the main bulletin)"
    )
    bulletin_parser.add_argument(
        "--date", default=None, help="Issue date YYYY-MM-DD (default: today, Rome)"
    )
    bulletin_parser.add_argument(
        "--force", action="store_true", help="Regenerate an existing issue"
    )
    bulletin_parser.add_argument(
        "--edition",
        default="daily",
        choices=sorted(EDITIONS),
        help="Edition to build (default: daily, the main issue)",
    )
    bulletin_parser.add_argument(
        "--out", default="./site", help="Output directory (default ./site)"
    )

    # weekly
    weekly_parser = subparsers.add_parser("weekly", help="Generate and render the weekly recap")
    weekly_parser.add_argument(
        "--week", default=None, help="ISO week like 2026-W44 (default: due week)"
    )
    weekly_parser.add_argument("--force", action="store_true", help="Regenerate an existing recap")
    weekly_parser.add_argument("--out", default="./site", help="Output directory (default ./site)")

    # run
    run_parser = subparsers.add_parser(
        "run",
        help="Scheduled entry point: migrate, collect, each edition (generate, deliver), weekly if due",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Publish to ./site and ./outbox, send no email, set no delivery stamps "
        "(collect and generation still write to the configured DB)",
    )
    run_parser.add_argument("--to", default=None, help="Email recipient override")

    # sync-vault
    sync_parser = subparsers.add_parser("sync-vault", help="Pull the vault outbox into Obsidian")
    sync_parser.add_argument(
        "--outbox", default=None, help="Local outbox dir instead of DATA_BUCKET"
    )
    sync_parser.add_argument("--vault", default=None, help="Vault dir (default VAULT_PATH)")
    sync_parser.add_argument("--no-opml", action="store_true", help="Do not upload the local OPML")

    # list
    list_parser = subparsers.add_parser("list", help="List recent articles")
    list_parser.add_argument("--tier", type=str, default=None, help="Filter by tier")
    list_parser.add_argument("--limit", type=int, default=50, help="Max articles to show")

    # reset
    subparsers.add_parser("reset", help="Reset all triaged/integrated articles for re-processing")

    # import-opml
    import_parser = subparsers.add_parser("import-opml", help="Import feeds from OPML file")
    import_parser.add_argument("file", type=str, help="Path to OPML file")
    import_parser.add_argument("--sync", action="store_true", help="Deactivate feeds not in OPML")

    args = parser.parse_args()
    commands = {
        "migrate": cmd_migrate,
        "fetch": cmd_fetch,
        "reextract": cmd_reextract,
        "triage": cmd_triage,
        "analyze": cmd_analyze,
        "integrate": cmd_integrate,
        "collect": cmd_collect,
        "bulletin": cmd_bulletin,
        "weekly": cmd_weekly,
        "run": cmd_run,
        "serve": cmd_serve,
        "sync-vault": cmd_sync_vault,
        "list": cmd_list,
        "reset": cmd_reset,
        "import-opml": cmd_import_opml,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
