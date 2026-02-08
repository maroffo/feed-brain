# ABOUTME: RSS feed fetcher that discovers and stores new articles.
# ABOUTME: Parses RSS/Atom feeds, extracts content, and persists to SQLite.

import asyncio
import contextlib
from datetime import UTC, datetime

import feedparser
import structlog
from sqlalchemy import select

from feed_brain.config import get_settings
from feed_brain.db.models import Article, FeedSource
from feed_brain.db.session import get_session_factory
from feed_brain.services.extractor import extract_content

log = structlog.get_logger()


async def fetch_all_feeds() -> int:
    """Fetch articles from all active feed sources.

    Returns the number of new articles stored.
    """
    settings = get_settings()
    session_factory = get_session_factory()
    total_new = 0

    async with session_factory() as session:
        result = await session.execute(select(FeedSource).where(FeedSource.active.is_(True)))
        sources = result.scalars().all()

        if not sources:
            log.warning("no_active_feeds")
            return 0

        for source in sources:
            new_count = await _fetch_single_feed(session, source, settings)
            total_new += new_count

        await session.commit()

    log.info("fetch_complete", total_new=total_new, feeds=len(sources))
    return total_new


async def _fetch_single_feed(session, source: FeedSource, settings) -> int:
    """Fetch and store articles from a single feed source."""
    log.info("fetching_feed", name=source.name, url=source.url)

    feed = await asyncio.to_thread(feedparser.parse, source.url, agent=settings.feed_user_agent)
    if feed.bozo:
        log.error("feed_parse_error", name=source.name, error=str(feed.bozo_exception))
        return 0

    new_count = 0
    entries = feed.entries[: settings.max_articles_per_feed]

    for entry in entries:
        url = getattr(entry, "link", None)
        if not url:
            continue

        # Skip if already stored
        existing = await session.execute(select(Article.id).where(Article.url == url))
        if existing.scalar_one_or_none() is not None:
            continue

        title = getattr(entry, "title", "Untitled")
        author = getattr(entry, "author", None)

        # Parse published date
        published_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            with contextlib.suppress(ValueError, TypeError):
                published_date = datetime(*entry.published_parsed[:6], tzinfo=UTC)

        # Extract content
        content = await extract_content(url, settings)

        article = Article(
            url=url,
            title=title,
            author=author,
            source_id=source.id,
            content=content,
            published_date=published_date,
            fetched_at=datetime.now(UTC),
        )
        session.add(article)
        new_count += 1
        log.info("article_stored", title=title, url=url)

    return new_count
