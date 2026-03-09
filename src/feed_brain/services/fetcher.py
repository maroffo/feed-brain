# ABOUTME: Concurrent RSS feed fetcher with age cutoff and volume control.
# ABOUTME: Fetches feeds concurrently, writes to DB sequentially (SQLite single-writer).

import asyncio
import contextlib
from dataclasses import dataclass, field
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


@dataclass
class FetchedArticle:
    """Article data collected during concurrent fetch, before DB write."""

    url: str
    title: str
    author: str | None
    source_id: int
    content: str | None
    published_date: datetime | None


@dataclass
class FeedResult:
    """Result of fetching a single feed."""

    source_name: str
    articles: list[FetchedArticle] = field(default_factory=list)
    error: str | None = None


async def fetch_all_feeds() -> int:
    """Fetch articles from all active feed sources concurrently.

    HTTP fetching and content extraction run in parallel.
    DB writes are serialized to avoid SQLite locking.
    Returns the number of new articles stored.
    """
    settings = get_settings()
    session_factory = get_session_factory()
    semaphore = asyncio.Semaphore(settings.fetch_concurrency)

    # Load sources
    async with session_factory() as session:
        result = await session.execute(select(FeedSource).where(FeedSource.active.is_(True)))
        sources = result.scalars().all()
        # Collect existing URLs to skip duplicates
        existing_result = await session.execute(select(Article.url))
        existing_urls = {row[0] for row in existing_result.all()}

    if not sources:
        log.warning("no_active_feeds")
        return 0

    # Fetch all feeds concurrently (I/O-bound)
    async def _bounded_fetch(source: FeedSource) -> FeedResult:
        async with semaphore:
            return await _fetch_single_feed(source, settings, existing_urls)

    results = await asyncio.gather(
        *[_bounded_fetch(s) for s in sources],
        return_exceptions=True,
    )

    # Collect all articles from successful fetches
    all_articles: list[FetchedArticle] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            log.error("feed_fetch_exception", name=sources[i].name, error=str(r))
        elif r.error:
            pass  # Already logged in _fetch_single_feed
        else:
            all_articles.extend(r.articles)

    # Write all articles to DB sequentially (SQLite single-writer)
    total_new = 0
    if all_articles:
        async with session_factory() as session:
            for fetched in all_articles:
                # Re-check URL uniqueness (another feed may have same article)
                existing = await session.execute(
                    select(Article.id).where(Article.url == fetched.url)
                )
                if existing.scalar_one_or_none() is not None:
                    continue

                article = Article(
                    url=fetched.url,
                    title=fetched.title,
                    author=fetched.author,
                    source_id=fetched.source_id,
                    content=fetched.content,
                    published_date=fetched.published_date,
                    status="new",
                    fetched_at=datetime.now(UTC),
                )
                session.add(article)
                total_new += 1

            await session.commit()

    log.info("fetch_complete", total_new=total_new, feeds=len(sources))
    return total_new


async def _fetch_single_feed(
    source: FeedSource, settings: Settings, existing_urls: set[str]
) -> FeedResult:
    """Fetch and extract articles from a single feed. No DB access."""
    log.info("fetching_feed", name=source.name, url=source.url)
    result = FeedResult(source_name=source.name)

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
        result.error = str(e)
        return result

    feed = await asyncio.to_thread(feedparser.parse, xml_content)
    if feed.bozo and not feed.entries:
        log.error("feed_parse_error", name=source.name, error=str(feed.bozo_exception))
        result.error = str(feed.bozo_exception)
        return result

    cutoff = datetime.now(UTC) - timedelta(hours=settings.article_max_age_hours)
    entries = feed.entries[: settings.max_articles_per_feed]

    for entry in entries:
        url = getattr(entry, "link", None)
        if not url:
            continue

        # Skip if already in DB
        if url in existing_urls:
            continue

        # Parse published date and apply age cutoff
        published_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            with contextlib.suppress(ValueError, TypeError):
                published_date = datetime(*entry.published_parsed[:6], tzinfo=UTC)

        if published_date and published_date < cutoff:
            continue

        # Extract content (I/O-bound, runs concurrently)
        content = await extract_content(url, settings)

        # Track URL to avoid cross-feed duplicates within this batch
        existing_urls.add(url)

        result.articles.append(
            FetchedArticle(
                url=url,
                title=getattr(entry, "title", "Untitled"),
                author=getattr(entry, "author", None),
                source_id=source.id,
                content=content,
                published_date=published_date,
            )
        )
        log.info("article_fetched", title=getattr(entry, "title", "Untitled"), url=url)

    return result
