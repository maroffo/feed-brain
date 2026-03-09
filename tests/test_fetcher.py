# ABOUTME: Tests for RSS feed fetching and article storage.
# ABOUTME: Uses mock feeds and HTTP responses to verify fetch pipeline.

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from feed_brain.db.models import Article, FeedSource
from feed_brain.services.fetcher import _fetch_single_feed


def _make_feed_entry(url="https://example.com/post-1", title="Test Post", author="Author"):
    """Create a mock feedparser entry."""

    class Entry:
        pass

    entry = Entry()
    entry.link = url
    entry.title = title
    entry.author = author
    # Use current time to avoid age cutoff
    now = datetime.now(UTC)
    entry.published_parsed = (now.year, now.month, now.day, now.hour, 0, 0, 0, 0, 0)
    return entry


def _make_parsed_feed(entries):
    """Create a mock feedparser result."""

    class Feed:
        pass

    feed = Feed()
    feed.bozo = False
    feed.bozo_exception = None
    feed.entries = entries
    return feed


def _mock_httpx_response(text="<rss></rss>"):
    """Create a mock httpx response for feed download."""
    mock_response = MagicMock()
    mock_response.text = text
    mock_response.raise_for_status = MagicMock()
    return mock_response


class MockSettings:
    feed_user_agent = "test"
    max_articles_per_feed = 15
    feed_timeout = 5
    article_max_age_hours = 48


async def test_fetch_stores_new_article(db_session):
    """New articles are extracted and stored in the database."""
    source = FeedSource(name="Test", url="https://test.com/feed.xml")
    db_session.add(source)
    await db_session.flush()

    entries = [_make_feed_entry()]
    mock_feed = _make_parsed_feed(entries)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_httpx_response())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("feed_brain.services.fetcher.httpx.AsyncClient", return_value=mock_client),
        patch("feed_brain.services.fetcher.feedparser.parse", return_value=mock_feed),
        patch(
            "feed_brain.services.fetcher.extract_content",
            new_callable=AsyncMock,
            return_value="Extracted content here.",
        ),
    ):
        count = await _fetch_single_feed(db_session, source, MockSettings())

    assert count == 1
    result = await db_session.execute(select(Article))
    article = result.scalar_one()
    assert article.title == "Test Post"
    assert article.content == "Extracted content here."
    assert article.source_id == source.id
    assert article.status == "new"


async def test_fetch_skips_existing_url(db_session):
    """Articles with URLs already in DB are skipped."""
    source = FeedSource(name="Test", url="https://test.com/feed.xml")
    db_session.add(source)
    await db_session.flush()

    existing = Article(
        url="https://example.com/post-1",
        title="Old",
        source_id=source.id,
        fetched_at=datetime.now(UTC),
    )
    db_session.add(existing)
    await db_session.flush()

    entries = [_make_feed_entry(url="https://example.com/post-1")]
    mock_feed = _make_parsed_feed(entries)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_httpx_response())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("feed_brain.services.fetcher.httpx.AsyncClient", return_value=mock_client),
        patch("feed_brain.services.fetcher.feedparser.parse", return_value=mock_feed),
    ):
        count = await _fetch_single_feed(db_session, source, MockSettings())

    assert count == 0


async def test_fetch_handles_bozo_feed(db_session):
    """Malformed feeds with no entries return 0 without crashing."""
    source = FeedSource(name="Bad", url="https://bad.com/feed.xml")
    db_session.add(source)
    await db_session.flush()

    mock_feed = _make_parsed_feed([])
    mock_feed.bozo = True
    mock_feed.bozo_exception = Exception("bad XML")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_httpx_response())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("feed_brain.services.fetcher.httpx.AsyncClient", return_value=mock_client),
        patch("feed_brain.services.fetcher.feedparser.parse", return_value=mock_feed),
    ):
        count = await _fetch_single_feed(db_session, source, MockSettings())

    assert count == 0
