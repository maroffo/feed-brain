# ABOUTME: Tests for the Obsidian clipping generator.
# ABOUTME: Verifies markdown file creation, format, and edge cases.

from datetime import UTC, datetime

from feed_brain.db.models import Article
from feed_brain.services.clipping import _sanitize_filename, create_clipping


async def test_create_clipping_writes_file(tmp_path):
    """Approved article creates a markdown file in clippings dir."""
    article = Article(
        url="https://example.com/great-article",
        title="A Great Article About AI",
        author="Jane Doe",
        content="This is the full article content about AI agents.",
        summary="Overview of AI agent patterns.",
        published_date=datetime(2026, 2, 8, tzinfo=UTC),
    )

    result = await create_clipping(article, clippings_dir=tmp_path)

    assert result is True
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert 'title: "A Great Article About AI"' in content
    assert 'source: "https://example.com/great-article"' in content
    assert "Jane Doe" in content
    assert "clippings" in content
    assert "feed-brain" in content
    assert "This is the full article content" in content


async def test_create_clipping_missing_dir():
    """Returns False when clippings directory doesn't exist."""
    from pathlib import Path

    article = Article(url="https://example.com/x", title="X", content="content")
    result = await create_clipping(article, clippings_dir=Path("/nonexistent/dir"))
    assert result is False


async def test_create_clipping_existing_file(tmp_path):
    """Returns True without overwriting if file already exists."""
    article = Article(url="https://example.com/dup", title="Dup Article", content="new content")

    existing = tmp_path / "Dup Article.md"
    existing.write_text("original content")

    result = await create_clipping(article, clippings_dir=tmp_path)

    assert result is True
    assert existing.read_text() == "original content"


async def test_create_clipping_special_yaml_chars(tmp_path):
    """Titles with quotes and colons are safely escaped in YAML frontmatter."""
    article = Article(
        url="https://example.com/tricky",
        title='Why "AI Agents" Are the Future: A Deep Dive',
        author="O'Brien",
        content="Content here.",
        summary='Summary with "quotes" and colons: here.',
    )

    result = await create_clipping(article, clippings_dir=tmp_path)
    assert result is True
    content = list(tmp_path.glob("*.md"))[0].read_text()
    # json.dumps properly escapes quotes inside the string
    assert r"\"AI Agents\"" in content
    assert "O'Brien" in content


def test_sanitize_filename():
    """Filenames are cleaned of invalid characters."""
    assert _sanitize_filename('Test: A "Great" Article?') == "Test A Great Article"
    assert _sanitize_filename("Normal Title") == "Normal Title"
    assert len(_sanitize_filename("x" * 300)) <= 200
