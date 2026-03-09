# ABOUTME: Concurrent RSS feed fetcher with age cutoff and volume control.
# ABOUTME: Uses asyncio.gather with semaphore for parallel feed fetching.

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import feedparser
import httpx
import structlog
from sqlalchemy import select

from feed_brain.config import Settings, get_settings
from feed_brain.db.models import Article, FeedSource
from feed_brain.db.session import get_session_factory
from feed_brain.services.extractor import extract_content

log = structlog.get_logger()


async def fetch_all_feeds() -> int:
    """Fetch articles from all active feed sources concurrently.

    Each feed gets its own DB session to avoid concurrent AsyncSession usage.
    Returns the number of new articles stored.
    """
    settings = get_settings()
    session_factory = get_session_factory()
    semaphore = asyncio.Semaphore(settings.fetch_concurrency)

    # Load sources in a short-lived session
    async with session_factory() as session:
        result = await session.execute(select(FeedSource).where(FeedSource.active.is_(True)))
        sources = result.scalars().all()

    if not sources:
        log.warning("no_active_feeds")
        return 0

    async def _bounded_fetch(source: FeedSource) -> int:
        async with semaphore, session_factory() as session:
            count = await _fetch_single_feed(session, source, settings)
            await session.commit()
            return count

    results = await asyncio.gather(
        *[_bounded_fetch(s) for s in sources],
        return_exceptions=True,
    )

    total_new = 0
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            log.error("feed_fetch_exception", name=sources[i].name, error=str(r))
        else:
            total_new += r

    log.info("fetch_complete", total_new=total_new, feeds=len(sources))
    return total_new


async def _fetch_single_feed(session, source: FeedSource, settings: Settings) -> int:
    """Fetch and store articles from a single feed source."""
    log.info("fetching_feed", name=source.name, url=source.url)

    try:
        async with httpx.AsyncClient(
            timeout=settings.feed_timeout,
            headers={"User-Agent": settings.feed_user_agent},
            follow_redirects=True,
        ) as client:
            response = await client.get(source.url)
            response.raise_for_status()
            xml_content = response.text
    except httpx.HTTPError as e:
        log.error("feed_download_error", name=source.name, error=str(e))
        return 0

    feed = await asyncio.to_thread(feedparser.parse, xml_content)
    if feed.bozo and not feed.entries:
        log.error("feed_parse_error", name=source.name, error=str(feed.bozo_exception))
        return 0

    new_count = 0
    cutoff = datetime.now(UTC) - timedelta(hours=settings.article_max_age_hours)
    entries = feed.entries[: settings.max_articles_per_feed]

    for entry in entries:
        url = getattr(entry, "link", None)
        if not url:
            continue

        # Parse published date and apply age cutoff
        published_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            with contextlib.suppress(ValueError, TypeError):
                published_date = datetime(*entry.published_parsed[:6], tzinfo=UTC)

        if published_date and published_date < cutoff:
            continue

        # Skip if already stored
        existing = await session.execute(select(Article.id).where(Article.url == url))
        if existing.scalar_one_or_none() is not None:
            continue

        title = getattr(entry, "title", "Untitled")
        author = getattr(entry, "author", None)

        # Extract content
        content = await extract_content(url, settings)

        article = Article(
            url=url,
            title=title,
            author=author,
            source_id=source.id,
            content=content,
            published_date=published_date,
            status="new",
            fetched_at=datetime.now(UTC),
        )
        session.add(article)
        new_count += 1
        log.info("article_stored", title=title, url=url)

    return new_count
