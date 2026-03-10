# ABOUTME: CLI entry point for feed-brain with full pipeline commands.
# ABOUTME: Supports fetch, triage, analyze, integrate, digest, run, list, reset, and import-opml.

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


def cmd_integrate(_args: argparse.Namespace) -> None:
    """Push high-tier articles to Second Brain."""
    asyncio.run(_run_integrate())


async def _run_integrate() -> None:
    await _init()
    try:
        from feed_brain.services.integrator import integrate_articles

        integrated = await integrate_articles()
        log.info("integrate_done", integrated=integrated)
    finally:
        await _shutdown()


def cmd_digest(_args: argparse.Namespace) -> None:
    """Generate daily digest note in the vault."""
    asyncio.run(_run_digest())


async def _run_digest() -> None:
    await _init()
    try:
        from feed_brain.services.digest import generate_digest

        count = await generate_digest()
        log.info("digest_done", articles=count)
    finally:
        await _shutdown()


def cmd_run(_args: argparse.Namespace) -> None:
    """Full pipeline: fetch → triage → analyze → integrate → digest."""
    asyncio.run(_run_pipeline())


async def _run_pipeline() -> None:
    await _init()
    try:
        from feed_brain.services.analyzer import analyze_high_tier
        from feed_brain.services.digest import generate_digest
        from feed_brain.services.fetcher import fetch_all_feeds
        from feed_brain.services.integrator import integrate_articles
        from feed_brain.services.triage import triage_new_articles

        new = await fetch_all_feeds()
        log.info("pipeline_fetch_done", new_articles=new)

        triaged = await triage_new_articles()
        log.info("pipeline_triage_done", triaged=triaged)

        analyzed = await analyze_high_tier()
        log.info("pipeline_analyze_done", analyzed=analyzed)

        integrated = await integrate_articles()
        log.info("pipeline_integrate_done", integrated=integrated)

        digest_count = await generate_digest()
        log.info("pipeline_digest_done", articles=digest_count)
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


def cmd_reset(_args: argparse.Namespace) -> None:
    """Reset triaged/integrated articles back to 'new' for re-processing."""
    asyncio.run(_run_reset())


async def _run_reset() -> None:
    from sqlalchemy import update

    await _init()
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
            log.info("reset_done", articles_reset=result.rowcount)
            print(f"Reset {result.rowcount} articles to 'new' status.")
    finally:
        await _shutdown()


def cmd_import_opml(args: argparse.Namespace) -> None:
    """Import feeds from an OPML file."""
    asyncio.run(_run_import_opml(args.file, sync=args.sync))


async def _run_import_opml(filepath: str, sync: bool = False) -> None:
    from pathlib import Path

    from sqlalchemy import select

    await _init()
    try:
        from feed_brain.db.models import FeedSource
        from feed_brain.db.session import get_session_factory
        from feed_brain.services.opml import parse_opml

        content = Path(filepath).read_text(encoding="utf-8")
        feeds = parse_opml(content)
        opml_urls = {feed["url"] for feed in feeds}

        session_factory = get_session_factory()
        imported = 0
        reactivated = 0
        deactivated = 0

        async with session_factory() as session:
            # Import new feeds and reactivate existing ones in OPML
            for feed in feeds:
                result = await session.execute(
                    select(FeedSource).where(FeedSource.url == feed["url"])
                )
                existing = result.scalar_one_or_none()
                if existing:
                    if not existing.active:
                        existing.active = True
                        reactivated += 1
                else:
                    session.add(FeedSource(name=feed["name"], url=feed["url"]))
                    imported += 1

            # With --sync, deactivate feeds not in OPML
            if sync:
                all_feeds = await session.execute(select(FeedSource))
                for source in all_feeds.scalars().all():
                    if source.url not in opml_urls and source.active:
                        source.active = False
                        deactivated += 1

            await session.commit()

        print(f"Imported {imported} new feeds (of {len(feeds)} total in OPML).")
        if reactivated:
            print(f"Reactivated {reactivated} feeds.")
        if deactivated:
            print(f"Deactivated {deactivated} feeds not in OPML.")
        log.info(
            "opml_import_done",
            imported=imported,
            reactivated=reactivated,
            deactivated=deactivated,
            total=len(feeds),
        )
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
    subparsers.add_parser("integrate", help="Push high-tier articles to Second Brain")

    # digest
    subparsers.add_parser("digest", help="Generate daily digest note in vault")

    # run
    subparsers.add_parser("run", help="Full pipeline: fetch→triage→analyze→integrate→digest")

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
        "fetch": cmd_fetch,
        "triage": cmd_triage,
        "analyze": cmd_analyze,
        "integrate": cmd_integrate,
        "digest": cmd_digest,
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
