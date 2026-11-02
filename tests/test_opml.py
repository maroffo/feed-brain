# ABOUTME: Tests for OPML feed import parsing.
# ABOUTME: Verifies standard OPML format, edge cases, and invalid XML handling.

from feed_brain.services.opml import parse_opml

SAMPLE_OPML = """\
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>My Feeds</title></head>
  <body>
    <outline text="Tech" title="Tech">
      <outline text="Simon Willison"
               title="Simon Willison"
               type="rss"
               xmlUrl="https://simonwillison.net/atom/everything/"
               htmlUrl="https://simonwillison.net/" />
      <outline text="Julia Evans"
               title="Julia Evans"
               type="rss"
               xmlUrl="https://jvns.ca/atom.xml"
               htmlUrl="https://jvns.ca/" />
    </outline>
    <outline text="News" title="News">
      <outline text="Hacker News"
               type="rss"
               xmlUrl="https://hnrss.org/frontpage" />
    </outline>
  </body>
</opml>
"""


def test_parse_opml_extracts_feeds():
    """Standard OPML with nested outlines extracts all feeds."""
    feeds = parse_opml(SAMPLE_OPML)
    assert len(feeds) == 3
    assert feeds[0]["name"] == "Simon Willison"
    assert feeds[0]["url"] == "https://simonwillison.net/atom/everything/"
    assert feeds[1]["name"] == "Julia Evans"
    assert feeds[2]["name"] == "Hacker News"
    assert feeds[2]["url"] == "https://hnrss.org/frontpage"


def test_parse_opml_skips_folders():
    """Folder outlines (no xmlUrl) are skipped."""
    feeds = parse_opml(SAMPLE_OPML)
    names = [f["name"] for f in feeds]
    assert "Tech" not in names
    assert "News" not in names


def test_parse_opml_invalid_xml():
    """Invalid XML returns empty list."""
    feeds = parse_opml("this is not xml at all")
    assert feeds == []


def test_parse_opml_empty():
    """Empty OPML body returns empty list."""
    opml = '<?xml version="1.0"?><opml><head/><body/></opml>'
    feeds = parse_opml(opml)
    assert feeds == []


def test_parse_opml_uses_text_fallback():
    """Falls back to text attribute when title is missing."""
    opml = """\
<?xml version="1.0"?>
<opml version="2.0">
  <body>
    <outline text="My Blog" xmlUrl="https://example.com/feed.xml" />
  </body>
</opml>
"""
    feeds = parse_opml(opml)
    assert len(feeds) == 1
    assert feeds[0]["name"] == "My Blog"


def test_parse_opml_records_folder():
    """Feeds nested in a folder outline carry the folder title; top-level feeds get None."""
    opml = """\
<?xml version="1.0"?>
<opml version="2.0">
  <body>
    <outline text="0. Security" title="0. Security">
      <outline text="Krebs" xmlUrl="https://krebsonsecurity.com/feed/" />
    </outline>
    <outline text="Loose" xmlUrl="https://example.com/loose.xml" />
  </body>
</opml>
"""
    feeds = parse_opml(opml)
    by_name = {f["name"]: f for f in feeds}
    assert by_name["Krebs"]["folder"] == "0. Security"
    assert by_name["Loose"]["folder"] is None


def test_parse_opml_nested_folders_use_innermost():
    """A feed inside nested folders takes the innermost folder title."""
    opml = """\
<?xml version="1.0"?>
<opml version="2.0">
  <body>
    <outline text="News">
      <outline text="Italy">
        <outline text="Il Post" xmlUrl="https://www.ilpost.it/feed/" />
      </outline>
    </outline>
  </body>
</opml>
"""
    feeds = parse_opml(opml)
    assert feeds == [{"name": "Il Post", "url": "https://www.ilpost.it/feed/", "folder": "Italy"}]


async def test_sync_feed_sources_imports_and_updates_folder(db_session):
    """New feeds are added with folder; known feeds get folder refreshed and reactivated."""
    from sqlalchemy import select

    from feed_brain.db.models import FeedSource
    from feed_brain.services.opml import sync_feed_sources

    db_session.add(FeedSource(name="Old", url="https://a.example/feed", folder=None, active=False))
    await db_session.commit()

    feeds = [
        {"name": "A", "url": "https://a.example/feed", "folder": "News"},
        {"name": "B", "url": "https://b.example/feed", "folder": "Blog"},
    ]
    stats = await sync_feed_sources(db_session, feeds, deactivate_missing=False)
    await db_session.commit()

    assert (stats.imported, stats.reactivated, stats.deactivated) == (1, 1, 0)
    rows = {s.url: s for s in (await db_session.execute(select(FeedSource))).scalars()}
    assert rows["https://a.example/feed"].folder == "News"
    assert rows["https://a.example/feed"].active is True
    assert rows["https://b.example/feed"].folder == "Blog"


async def test_sync_feed_sources_deactivates_missing_only_when_asked(db_session):
    """Feeds absent from the OPML are deactivated only with deactivate_missing."""
    from feed_brain.db.models import FeedSource
    from feed_brain.services.opml import sync_feed_sources

    gone = FeedSource(name="Gone", url="https://gone.example/feed")
    db_session.add(gone)
    await db_session.commit()

    stats = await sync_feed_sources(db_session, [], deactivate_missing=False)
    assert stats.deactivated == 0
    assert gone.active is True

    stats = await sync_feed_sources(db_session, [], deactivate_missing=True)
    assert stats.deactivated == 1
    assert gone.active is False


async def test_sync_feed_sources_dedups_repeated_urls(db_session):
    """The same URL twice in one OPML is imported once."""
    from feed_brain.services.opml import sync_feed_sources

    feeds = [{"name": "R", "url": "https://r.example/rss", "folder": "Sec"}] * 2
    stats = await sync_feed_sources(db_session, feeds, deactivate_missing=False)
    await db_session.commit()
    assert stats.imported == 1
