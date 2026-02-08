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
