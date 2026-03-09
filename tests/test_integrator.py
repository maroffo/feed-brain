# ABOUTME: Tests for Second Brain integrator.
# ABOUTME: Verifies note creation, filename sanitization, and deep section building.

from feed_brain.db.models import Article
from feed_brain.services.integrator import _build_deep_section, _sanitize_filename, _write_note


def test_sanitize_filename():
    """Filenames are cleaned of invalid characters."""
    assert _sanitize_filename('Test: A "Great" Article?') == "Test A Great Article"
    assert _sanitize_filename("Normal Title") == "Normal Title"
    assert len(_sanitize_filename("x" * 300)) <= 200


def test_write_note_creates_file(tmp_path):
    """Article is written as a markdown note to the vault."""
    article = Article(
        url="https://example.com/article",
        title="AI Agents Overview",
        author="Jane Doe",
        content="Full article content here.",
        summary="Summary of AI agents article.",
        tier="high",
        category="ai_agents",
        confidence=0.95,
        status="analyzed",
    )

    target = _write_note(article, tmp_path)

    assert target is not None
    files = list(tmp_path.rglob("*.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "AI Agents Overview" in content
    assert "https://example.com/article" in content
    assert "Summary of AI agents article." in content
    assert "feed-brain" in content


def test_write_note_creates_subdirectory(tmp_path):
    """Category-based subdirectory is created automatically."""
    article = Article(
        url="https://example.com/devops",
        title="K8s Best Practices",
        summary="Kubernetes tips.",
        tier="high",
        category="devops_cloud",
        confidence=0.88,
        status="analyzed",
    )

    target = _write_note(article, tmp_path)

    assert target is not None
    assert "Resources/DevOps" in target


def test_write_note_skips_existing(tmp_path):
    """Existing notes are not overwritten."""
    article = Article(
        url="https://example.com/dup",
        title="Dup Article",
        summary="Summary.",
        tier="high",
        category="development",
        confidence=0.9,
        status="analyzed",
    )

    # Create directory and file first
    target_dir = tmp_path / "Resources" / "Development"
    target_dir.mkdir(parents=True)
    (target_dir / "Dup Article.md").write_text("original content")

    target = _write_note(article, tmp_path)
    assert target is not None
    assert (target_dir / "Dup Article.md").read_text() == "original content"


def test_build_deep_section_with_all_fields():
    """Deep section includes all analysis fields."""
    article = Article(
        url="https://example.com/deep",
        title="Deep Article",
        deep_summary="Detailed analysis of the topic.",
        deep_insights='["Insight one", "Insight two"]',
        money_quote="The best code is no code.",
        actionables='["Try X", "Use Y"]',
    )

    section = _build_deep_section(article)
    assert "## Deep Analysis" in section
    assert "Detailed analysis" in section
    assert "Insight one" in section
    assert "The best code is no code." in section
    assert "Try X" in section


def test_build_deep_section_empty():
    """Empty deep section returns empty string."""
    article = Article(url="https://example.com/empty", title="Empty")
    section = _build_deep_section(article)
    assert section == ""
