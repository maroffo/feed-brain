# ABOUTME: CLI entry point for feed-brain with full pipeline commands.
# ABOUTME: Commands: migrate, fetch, triage, collect, run, analyze, integrate, list, reset, import-opml.

import argparse
import asyncio
import logging
import sys

import structlog


def _configure_logging() -> None:
    """Configure structlog with human-readable output, no debug."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ],
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


def cmd_fetch(_args: argparse.Namespace) -> None:
    """Fetch new articles from all feeds."""
    asyncio.run(_run_fetch())


async def _run_fetch() -> None:
    try:
        from feed_brain.services.fetcher import fetch_all_feeds

        new = await fetch_all_feeds()
        log.info("fetch_done", new_articles=new)
    finally:
        await _shutdown()


def cmd_triage(_args: argparse.Namespace) -> None:
    """Triage new articles with Gemini."""
    asyncio.run(_run_triage())


async def _run_triage() -> None:
    try:
        from feed_brain.services.triage import triage_new_articles

        triaged = await triage_new_articles()
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


def cmd_collect(_args: argparse.Namespace) -> None:
    """Fetch all feeds, then triage the new articles."""
    asyncio.run(_run_collect())


async def _run_collect() -> None:
    try:
        await _collect()
    finally:
        await _shutdown()


async def _collect() -> None:
    from feed_brain.services.fetcher import fetch_all_feeds
    from feed_brain.services.triage import triage_new_articles

    new = await fetch_all_feeds()
    triaged = await triage_new_articles()
    log.info("collect_done", new_articles=new, triaged=triaged)


def cmd_run(_args: argparse.Namespace) -> None:
    """Scheduled entry point: migrate, then collect."""
    from feed_brain.db.migrate import upgrade_head

    upgrade_head()
    asyncio.run(_run_collect())


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
    from pathlib import Path

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

    # fetch
    subparsers.add_parser("fetch", help="Fetch new articles from all feeds")

    # triage
    subparsers.add_parser("triage", help="Triage new articles with Gemini")

    # analyze
    analyze_parser = subparsers.add_parser("analyze", help="Deep-analyze one article with Opus")
    analyze_parser.add_argument("--article-id", type=int, required=True, help="Article id")

    # integrate
    subparsers.add_parser("integrate", help="Push high-tier articles to Second Brain")

    # collect
    subparsers.add_parser("collect", help="Fetch feeds and triage new articles")

    # run
    subparsers.add_parser("run", help="Scheduled entry point: migrate, then collect")

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
        "triage": cmd_triage,
        "analyze": cmd_analyze,
        "integrate": cmd_integrate,
        "collect": cmd_collect,
        "run": cmd_run,
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
