# ABOUTME: CLI entry point for feed-brain.
# ABOUTME: Supports 'serve' and 'fetch' commands.

import argparse
import sys

import structlog
import uvicorn

from feed_brain.config import get_settings

log = structlog.get_logger()


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the web UI server."""
    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    log.info("starting_server", host=host, port=port)
    uvicorn.run("feed_brain.web.app:app", host=host, port=port, reload=args.reload)


def cmd_fetch(_args: argparse.Namespace) -> None:
    """Fetch and classify articles from all active feeds."""
    import asyncio

    asyncio.run(_run_fetch())


async def _run_fetch() -> None:
    """Async fetch pipeline: fetch feeds, extract content, classify."""
    from feed_brain.db.session import close_db, init_db

    await init_db()
    try:
        from feed_brain.services.classifier import classify_unclassified
        from feed_brain.services.fetcher import fetch_all_feeds

        new = await fetch_all_feeds()
        log.info("fetch_done", new_articles=new)
        classified = await classify_unclassified()
        log.info("classify_done", classified=classified)
    finally:
        await close_db()


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(prog="feed-brain", description="AI-powered feed aggregator")
    subparsers = parser.add_subparsers(dest="command")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start web UI")
    serve_parser.add_argument("--host", type=str, default=None)
    serve_parser.add_argument("--port", type=int, default=None)
    serve_parser.add_argument("--reload", action="store_true")

    # fetch
    subparsers.add_parser("fetch", help="Fetch and classify feeds")

    args = parser.parse_args()
    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
