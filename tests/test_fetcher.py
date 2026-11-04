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


async def test_fetch_all_feeds_downloads_only_rss_sources(session_factory, monkeypatch):
    """Newsletter inbox sources have no feed URL to download."""
    from feed_brain.services import fetcher

    async with session_factory() as session:
        session.add_all(
            [
                FeedSource(name="RSS", url="https://rss.example/feed", feed_type="rss"),
                FeedSource(name="Inbox", url="email:x@example.com", feed_type="email"),
            ]
        )
        await session.commit()

    fetched: list[str] = []

    async def fake_fetch(source, settings, existing_urls):  # noqa: ARG001
        fetched.append(source.name)
        return fetcher.FeedResult(source_name=source.name)

    monkeypatch.setattr(fetcher, "_fetch_single_feed", fake_fetch)
    await fetcher.fetch_all_feeds()

    assert fetched == ["RSS"]


def test_the_article_link_wins_over_an_image_enclosure():
    """DDay.it entries carry the article as rel=alternate and a JPEG as enclosure; feedparser's
    `link` is the JPEG."""
    from feedparser import FeedParserDict as D

    from feed_brain.services.fetcher import _entry_url

    article = "https://www.dday.it/redazione/59327/furto"
    image = "https://images.dday.it/x/w:329/plain/crop.jpg"
    dday = D(
        link=image,
        id=article,
        links=[
            D(rel="alternate", type="text/html", href=article),
            D(rel="enclosure", type="image/jpeg", href=image),
        ],
    )
    assert _entry_url(dday) == article
    assert _entry_url(D(link="https://blog/x")) == "https://blog/x"  # plain feeds keep their link
    only_enclosure = D(
        link=image,
        id="https://site/post",
        links=[D(rel="enclosure", type="image/jpeg", href=image)],
    )
    assert _entry_url(only_enclosure) == "https://site/post"
    assert _entry_url(D(title="no link")) is None


async def test_reextract_refills_recent_short_articles_only(session_factory, monkeypatch):
    from datetime import timedelta

    from sqlalchemy import select

    from feed_brain.config import Settings
    from feed_brain.db.models import Article
    from feed_brain.services import fetcher

    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add_all(
            [
                Article(url="https://p/teaser", title="t", content="teaser", fetched_at=now),
                Article(url="https://p/full", title="t", content="x" * 3000, fetched_at=now),
                Article(
                    url="https://p/old",
                    title="t",
                    content="old",
                    fetched_at=now - timedelta(days=5),
                ),
                Article(url="https://p/still", title="t", content="short", fetched_at=now),
            ]
        )
        await session.commit()

    async def fake_extract(url, settings):  # noqa: ARG001
        return {"https://p/teaser": "testo completo " * 200, "https://p/still": "brev"}.get(url)

    monkeypatch.setattr(fetcher, "extract_content", fake_extract)
    refilled = await fetcher.reextract_short(
        hours=48, min_chars=1500, settings=Settings(_env_file=None)
    )

    async with session_factory() as session:
        contents = {a.url: a.content for a in (await session.execute(select(Article))).scalars()}
    assert refilled == 1
    assert contents["https://p/teaser"].startswith("testo completo")
    assert contents["https://p/full"] == "x" * 3000 and contents["https://p/old"] == "old"
    assert contents["https://p/still"] == "short"  # a shorter result never replaces the stored text


async def test_reextract_measures_visible_text_and_skips_inbox_mails(session_factory, monkeypatch):
    from sqlalchemy import select

    from feed_brain.config import Settings
    from feed_brain.db.models import Article
    from feed_brain.services import fetcher

    heavy_teaser = (
        "<div>" + '<img src="https://cdn/x.jpg" alt="foto">' * 60 + "<p>Solo l'attacco.</p></div>"
    )
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add_all(
            [
                Article(url="https://p/heavy", title="t", content=heavy_teaser, fetched_at=now),
                Article(url="mid:abc@mail", title="t", content="breve", fetched_at=now),
            ]
        )
        await session.commit()
    asked = []

    async def fake_extract(url, settings):  # noqa: ARG001
        asked.append(url)
        return "testo completo " * 150  # 2250 chars of text, fewer raw chars than the teaser's HTML

    monkeypatch.setattr(fetcher, "extract_content", fake_extract)
    refilled = await fetcher.reextract_short(
        hours=48, min_chars=1500, settings=Settings(_env_file=None)
    )
    async with session_factory() as session:
        contents = {a.url: a.content for a in (await session.execute(select(Article))).scalars()}
    assert asked == ["https://p/heavy"] and refilled == 1
    assert contents["https://p/heavy"].startswith("testo completo")
