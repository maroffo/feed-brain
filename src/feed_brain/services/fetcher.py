# ABOUTME: Concurrent RSS feed fetcher with age cutoff and volume control.
# ABOUTME: Fetches feeds concurrently, then writes new articles in a single session.

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import feedparser
import httpx
import structlog
from bs4 import BeautifulSoup
from sqlalchemy import func, select, update

from feed_brain.config import Settings, get_settings
from feed_brain.db.models import RSS_FEED_TYPE, Article, FeedSource
from feed_brain.db.session import get_session_factory
from feed_brain.services import http
from feed_brain.services.extractor import extract_content, visible_length
from feed_brain.textutil import clean_text

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
    DB writes happen afterwards in one session (one AsyncSession is not task-safe).
    Returns the number of new articles stored.
    """
    settings = get_settings()
    session_factory = get_session_factory()
    semaphore = asyncio.Semaphore(settings.fetch_concurrency)

    # Load sources
    async with session_factory() as session:
        result = await session.execute(
            select(FeedSource).where(
                FeedSource.active.is_(True), FeedSource.feed_type == RSS_FEED_TYPE
            )
        )
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
        if isinstance(r, BaseException):
            log.error("feed_fetch_exception", name=sources[i].name, error=str(r))
        elif r.error:
            pass  # Already logged in _fetch_single_feed
        else:
            all_articles.extend(r.articles)

    # Write all articles in one session
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
                    title=clean_text(fetched.title) or "Untitled",
                    author=clean_text(fetched.author),
                    source_id=fetched.source_id,
                    content=clean_text(fetched.content),
                    published_date=fetched.published_date,
                    status="new",
                    fetched_at=datetime.now(UTC),
                )
                session.add(article)
                total_new += 1

            await session.commit()

    log.info("fetch_complete", total_new=total_new, feeds=len(sources))
    return total_new


def _entry_url(entry: object) -> str | None:
    """The article's web address. An HTML rel=alternate link wins: some feeds (DDay.it) put a
    cover image in `enclosure` and feedparser then reports it as `link`. An entry whose link
    is an enclosure falls back to its id when that is a web address."""
    links = getattr(entry, "links", None) or []
    for link in links:
        if (
            link.get("rel") == "alternate"
            and link.get("type", "text/html") in ("text/html", "")
            and link.get("href")
        ):
            return link["href"]
    enclosures = {link.get("href") for link in links if link.get("rel") == "enclosure"}
    url = getattr(entry, "link", None)
    if url and url not in enclosures:
        return url
    entry_id = getattr(entry, "id", None)
    if isinstance(entry_id, str) and entry_id.startswith(("http://", "https://")):
        return entry_id
    return None


def _entry_text(entry: object) -> str | None:
    """Plain text of the feed entry's summary, for pages that can't be extracted."""
    summary = getattr(entry, "summary", None)
    if not summary:
        return None
    text = BeautifulSoup(summary, "html.parser").get_text(" ", strip=True)
    return " ".join(text.split()) or None


async def _fetch_single_feed(
    source: FeedSource, settings: Settings, existing_urls: set[str]
) -> FeedResult:
    """Fetch and extract articles from a single feed. No DB access."""
    log.info("fetching_feed", name=source.name, url=source.url)
    result = FeedResult(source_name=source.name)

    try:
        xml_content = (await http.fetch(source.url, settings, kind="feed")).text
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
        url = _entry_url(entry)
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

        # Extract content (I/O-bound, runs concurrently); fall back to the feed's own summary
        content = await extract_content(url, settings) or _entry_text(entry)

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


async def reextract_short(hours: int, min_chars: int, settings: Settings | None = None) -> int:
    """Extract again the web articles fetched in the last `hours` whose stored text is shorter
    than `min_chars` visible characters (teasers, failed extractions). A result replaces the
    stored text only when it has more visible text. Returns how many articles got longer text."""
    settings = settings or get_settings()
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    factory = get_session_factory()
    async with factory() as session:
        stored = (
            await session.execute(
                select(Article.id, Article.url, Article.content)
                .where(Article.fetched_at >= cutoff)
                .where(Article.url.like("http%"))  # inbox mails (mid:...) have no page to fetch
                # raw length >= visible length: a generous prefilter, measured exactly below
                .where(func.coalesce(func.length(Article.content), 0) < min_chars * 4)
            )
        ).all()
    rows = [
        (article_id, url, length)
        for article_id, url, content in stored
        if (length := visible_length(content)) < min_chars
    ]
    semaphore = asyncio.Semaphore(settings.fetch_concurrency)

    async def _one(url: str) -> str | None:
        async with semaphore:
            return await extract_content(url, settings)

    results = await asyncio.gather(*(_one(url) for _, url, _ in rows))
    refilled = 0
    async with factory() as session:
        for (article_id, url, length), content in zip(rows, results, strict=True):
            text = clean_text(content)
            if text and visible_length(text) > length:
                await session.execute(
                    update(Article).where(Article.id == article_id).values(content=text)
                )
                refilled += 1
                log.info("article_reextracted", url=url, before=length, after=visible_length(text))
        await session.commit()
    log.info("reextract_done", candidates=len(rows), refilled=refilled)
    return refilled
