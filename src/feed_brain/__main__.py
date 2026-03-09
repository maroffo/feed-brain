# ABOUTME: CLI entry point for feed-brain with full pipeline commands.
# ABOUTME: Supports fetch, triage, analyze, integrate, run, list, and import-opml.

import argparse
import asyncio
import sys

import structlog

log = structlog.get_logger()


async def _init() -> None:
    """Initialize database and run migrations."""
    from feed_brain.db.session import init_db, migrate_db

    await init_db()
    await migrate_db()


async def _shutdown() -> None:
    """Clean up database connections."""
    from feed_brain.db.session import close_db

    await close_db()


def cmd_fetch(_args: argparse.Namespace) -> None:
    """Fetch new articles from all feeds."""
    asyncio.run(_run_fetch())


async def _run_fetch() -> None:
    await _init()
    try:
        from feed_brain.services.fetcher import fetch_all_feeds

        new = await fetch_all_feeds()
        log.info("fetch_done", new_articles=new)
    finally:
        await _shutdown()


def cmd_triage(_args: argparse.Namespace) -> None:
    """Triage unclassified articles with Ollama."""
    asyncio.run(_run_triage())


async def _run_triage() -> None:
    await _init()
    try:
        from feed_brain.services.triage import triage_new_articles

        triaged = await triage_new_articles()
        log.info("triage_done", triaged=triaged)
    finally:
        await _shutdown()


def cmd_analyze(_args: argparse.Namespace) -> None:
    """Run deep analysis on high-tier articles."""
    asyncio.run(_run_analyze())


async def _run_analyze() -> None:
    await _init()
    try:
        from feed_brain.services.analyzer import analyze_high_tier

        analyzed = await analyze_high_tier()
        log.info("analyze_done", analyzed=analyzed)
    finally:
        await _shutdown()


def cmd_integrate(args: argparse.Namespace) -> None:
    """Push articles to Second Brain."""
    asyncio.run(_run_integrate(args.auto))


async def _run_integrate(auto: bool) -> None:
    await _init()
    try:
        from feed_brain.services.integrator import integrate_articles

        integrated = await integrate_articles(auto=auto)
        log.info("integrate_done", integrated=integrated)
    finally:
        await _shutdown()


def cmd_run(args: argparse.Namespace) -> None:
    """Full pipeline: fetch → triage → analyze → integrate."""
    asyncio.run(_run_pipeline(args.auto))


async def _run_pipeline(auto: bool) -> None:
    await _init()
    try:
        from feed_brain.services.analyzer import analyze_high_tier
        from feed_brain.services.fetcher import fetch_all_feeds
        from feed_brain.services.integrator import integrate_articles
        from feed_brain.services.triage import triage_new_articles

        new = await fetch_all_feeds()
        log.info("pipeline_fetch_done", new_articles=new)

        triaged = await triage_new_articles()
        log.info("pipeline_triage_done", triaged=triaged)

        analyzed = await analyze_high_tier()
        log.info("pipeline_analyze_done", analyzed=analyzed)

        integrated = await integrate_articles(auto=auto)
        log.info("pipeline_integrate_done", integrated=integrated)
    finally:
        await _shutdown()


def cmd_list(args: argparse.Namespace) -> None:
    """List recent articles."""
    asyncio.run(_run_list(args.tier, args.limit))


async def _run_list(tier: str | None, limit: int) -> None:
    from sqlalchemy import select

    await _init()
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


def cmd_import_opml(args: argparse.Namespace) -> None:
    """Import feeds from an OPML file."""
    asyncio.run(_run_import_opml(args.file))


async def _run_import_opml(filepath: str) -> None:
    from pathlib import Path

    from sqlalchemy import select

    await _init()
    try:
        from feed_brain.db.models import FeedSource
        from feed_brain.db.session import get_session_factory
        from feed_brain.services.opml import parse_opml

        content = Path(filepath).read_text(encoding="utf-8")
        feeds = parse_opml(content)

        session_factory = get_session_factory()
        imported = 0

        async with session_factory() as session:
            for feed in feeds:
                existing = await session.execute(
                    select(FeedSource.id).where(FeedSource.url == feed["url"])
                )
                if existing.scalar_one_or_none() is not None:
                    continue

                session.add(FeedSource(name=feed["name"], url=feed["url"]))
                imported += 1

            await session.commit()

        log.info("opml_import_done", imported=imported, total=len(feeds))
        print(f"Imported {imported} new feeds (of {len(feeds)} total in OPML).")
    finally:
        await _shutdown()


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(prog="feed-brain", description="AI-powered feed aggregator")
    subparsers = parser.add_subparsers(dest="command")

    # fetch
    subparsers.add_parser("fetch", help="Fetch new articles from all feeds")

    # triage
    subparsers.add_parser("triage", help="Triage unclassified articles with Ollama")

    # analyze
    subparsers.add_parser("analyze", help="Deep analysis on high-tier articles")

    # integrate
    integrate_parser = subparsers.add_parser("integrate", help="Push articles to Second Brain")
    integrate_parser.add_argument("--auto", action="store_true", help="Skip interactive prompts")

    # run
    run_parser = subparsers.add_parser("run", help="Full pipeline: fetch→triage→analyze→integrate")
    run_parser.add_argument("--auto", action="store_true", help="Skip interactive prompts")

    # list
    list_parser = subparsers.add_parser("list", help="List recent articles")
    list_parser.add_argument("--tier", type=str, default=None, help="Filter by tier")
    list_parser.add_argument("--limit", type=int, default=50, help="Max articles to show")

    # import-opml
    import_parser = subparsers.add_parser("import-opml", help="Import feeds from OPML file")
    import_parser.add_argument("file", type=str, help="Path to OPML file")

    args = parser.parse_args()
    commands = {
        "fetch": cmd_fetch,
        "triage": cmd_triage,
        "analyze": cmd_analyze,
        "integrate": cmd_integrate,
        "run": cmd_run,
        "list": cmd_list,
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
