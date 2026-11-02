# ABOUTME: Tests for RSS feed fetching and article extraction.
# ABOUTME: Uses mock feeds and HTTP responses to verify fetch pipeline.

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from feed_brain.db.models import FeedSource
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
    page_user_agent = "page"
    max_articles_per_feed = 15
    feed_timeout = 5
    article_max_age_hours = 48
    fetch_proxy = None


async def test_fetch_extracts_new_article():
    """New articles are extracted and returned as FetchedArticle."""
    source = FeedSource(id=1, name="Test", url="https://test.com/feed.xml")
    entries = [_make_feed_entry()]
    mock_feed = _make_parsed_feed(entries)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_httpx_response())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("feed_brain.services.http.httpx.AsyncClient", return_value=mock_client),
        patch("feed_brain.services.fetcher.feedparser.parse", return_value=mock_feed),
        patch(
            "feed_brain.services.fetcher.extract_content",
            new_callable=AsyncMock,
            return_value="Extracted content here.",
        ),
    ):
        result = await _fetch_single_feed(source, MockSettings(), existing_urls=set())

    assert result.error is None
    assert len(result.articles) == 1
    assert result.articles[0].title == "Test Post"
    assert result.articles[0].content == "Extracted content here."
    assert result.articles[0].source_id == 1


async def test_fetch_skips_existing_url():
    """Articles with URLs already in DB are skipped."""
    source = FeedSource(id=1, name="Test", url="https://test.com/feed.xml")
    entries = [_make_feed_entry(url="https://example.com/post-1")]
    mock_feed = _make_parsed_feed(entries)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_httpx_response())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("feed_brain.services.http.httpx.AsyncClient", return_value=mock_client),
        patch("feed_brain.services.fetcher.feedparser.parse", return_value=mock_feed),
    ):
        result = await _fetch_single_feed(
            source, MockSettings(), existing_urls={"https://example.com/post-1"}
        )

    assert result.error is None
    assert len(result.articles) == 0


async def test_fetch_handles_bozo_feed():
    """Malformed feeds with no entries return error."""
    source = FeedSource(id=1, name="Bad", url="https://bad.com/feed.xml")
    mock_feed = _make_parsed_feed([])
    mock_feed.bozo = True
    mock_feed.bozo_exception = Exception("bad XML")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_httpx_response())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("feed_brain.services.http.httpx.AsyncClient", return_value=mock_client),
        patch("feed_brain.services.fetcher.feedparser.parse", return_value=mock_feed),
    ):
        result = await _fetch_single_feed(source, MockSettings(), existing_urls=set())

    assert result.error is not None
    assert len(result.articles) == 0


async def test_fetch_falls_back_to_entry_summary_when_extraction_fails():
    """Feeds whose pages can't be extracted (ransomfeed, cvefeed) keep the entry summary as text."""
    source = FeedSource(id=1, name="Ransom Feed", url="https://ransomfeed.example/rss")
    entry = _make_feed_entry(url="https://ransomfeed.example/v/1")
    entry.summary = "<p>LockBit claims <b>Acme SpA</b> (Italy)</p>"
    no_summary = _make_feed_entry(url="https://ransomfeed.example/v/2")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_httpx_response())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("feed_brain.services.http.httpx.AsyncClient", return_value=mock_client),
        patch(
            "feed_brain.services.fetcher.feedparser.parse",
            return_value=_make_parsed_feed([entry, no_summary]),
        ),
        patch(
            "feed_brain.services.fetcher.extract_content", new_callable=AsyncMock, return_value=None
        ),
    ):
        result = await _fetch_single_feed(source, MockSettings(), existing_urls=set())

    contents = {a.url: a.content for a in result.articles}
    assert contents["https://ransomfeed.example/v/1"] == "LockBit claims Acme SpA (Italy)"
    assert contents["https://ransomfeed.example/v/2"] is None


async def test_feed_download_uses_the_feed_user_agent():
    """RSS downloads identify as feed-brain, not as the browser UA used for article pages."""
    source = FeedSource(id=1, name="Test", url="https://test.com/feed.xml")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_httpx_response())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("feed_brain.services.http.httpx.AsyncClient", return_value=mock_client) as client,
        patch("feed_brain.services.fetcher.feedparser.parse", return_value=_make_parsed_feed([])),
    ):
        await _fetch_single_feed(source, MockSettings(), existing_urls=set())

    assert client.call_args.kwargs["headers"] == {"User-Agent": "test"}
