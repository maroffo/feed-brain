# ABOUTME: Tests for Second Brain integrator.
# ABOUTME: Verifies append-to-topic-file pattern, timeline logging, and entry formatting.

from feed_brain.db.models import Article
from feed_brain.services.integrator import (
    _build_entry,
    _build_timeline_entry,
    _resolve_topic_file,
    integrate_article,
)


class MockSettings:
    vault_path = None  # Set per test via tmp_path


def test_resolve_topic_file_known_category(tmp_path):
    """Known categories resolve to correct Second Brain topic file."""
    name, path = _resolve_topic_file("ai_agents", tmp_path)
    assert name == "Second Brain - AI Agents and Tools"
    assert path.name == "Second Brain - AI Agents and Tools.md"
    assert "Second Brain" in str(path.parent)


def test_resolve_topic_file_unknown_category(tmp_path):
    """Unknown categories fall back to Development."""
    name, path = _resolve_topic_file("nonexistent", tmp_path)
    assert name == "Second Brain - Development"


def test_resolve_topic_file_none_category(tmp_path):
    """None category falls back to Development."""
    name, path = _resolve_topic_file(None, tmp_path)
    assert name == "Second Brain - Development"


def test_build_entry_basic():
    """Entry uses what/why table format with source link."""
    article = Article(
        url="https://example.com/article",
        title="AI Agents Overview",
        summary="AI agents are transforming software development workflows.",
        reason="Relevant to AI tooling interests.",
    )
    entry = _build_entry(article)
    assert "### AI Agents Overview" in entry
    assert "| **What** |" in entry
    assert "| **Why** |" in entry
    assert "https://example.com/article" in entry
    assert "Source" in entry


def test_build_entry_with_deep_analysis():
    """Entry includes deep analysis extras when available."""
    article = Article(
        url="https://example.com/deep",
        title="Deep Article",
        summary="Summary here.",
        reason="Why it matters.",
        deep_summary="Detailed analysis of the topic with many nuances.",
        deep_insights='["Insight one", "Insight two"]',
        money_quote="The best code is no code.",
        actionables='["Try X", "Use Y"]',
    )
    entry = _build_entry(article)
    assert "The best code is no code." in entry
    assert "Insight one" in entry
    assert "Actionable:" in entry
    assert "Try X" in entry


def test_build_entry_prefers_deep_summary():
    """Entry uses deep_summary over triage summary when available."""
    article = Article(
        url="https://example.com/deep",
        title="Deep Article",
        summary="Triage summary.",
        reason="Why.",
        deep_summary="Much more detailed deep analysis summary.",
    )
    entry = _build_entry(article)
    assert "detailed deep analysis" in entry
    assert "Triage summary" not in entry


def test_build_timeline_entry():
    """Timeline entry follows the standard format."""
    article = Article(url="https://example.com/x", title="Test Article")
    entry = _build_timeline_entry(article, "Second Brain - Development")
    assert "| [Test Article] |" in entry
    assert "Source: feed-brain" in entry
    assert "-> Second Brain - Development.md" in entry


def test_integrate_article_appends_to_topic_file(tmp_path):
    """Article content is appended to the correct topic file."""
    settings = MockSettings()
    settings.vault_path = tmp_path

    article = Article(
        url="https://example.com/article",
        title="AI Agents Overview",
        summary="AI agents are transforming workflows in meaningful ways.",
        reason="Relevant to AI tooling.",
        tier="high",
        category="ai_agents",
        confidence=0.95,
        status="analyzed",
    )

    target = integrate_article(article, settings)

    assert target == "Second Brain - AI Agents and Tools"
    topic_file = tmp_path / "Second Brain" / "Second Brain - AI Agents and Tools.md"
    assert topic_file.exists()
    content = topic_file.read_text()
    assert "### AI Agents Overview" in content
    assert "| **What** |" in content


def test_integrate_article_logs_to_timeline(tmp_path):
    """Integration creates a Timeline entry."""
    settings = MockSettings()
    settings.vault_path = tmp_path

    article = Article(
        url="https://example.com/timeline-test",
        title="Timeline Test",
        summary="Testing timeline logging for articles.",
        reason="Test.",
        tier="high",
        category="development",
        confidence=0.9,
        status="analyzed",
    )

    integrate_article(article, settings)

    timeline = tmp_path / "Second Brain" / "Second Brain - Timeline.md"
    assert timeline.exists()
    content = timeline.read_text()
    assert "Timeline Test" in content
    assert "feed-brain" in content
    assert "Second Brain - Development.md" in content


def test_integrate_article_appends_not_overwrites(tmp_path):
    """Multiple articles append to the same topic file."""
    settings = MockSettings()
    settings.vault_path = tmp_path

    article1 = Article(
        url="https://example.com/first",
        title="First Article",
        summary="First article content for testing append behavior.",
        reason="Test.",
        category="development",
    )
    article2 = Article(
        url="https://example.com/second",
        title="Second Article",
        summary="Second article content for testing append behavior.",
        reason="Test.",
        category="development",
    )

    integrate_article(article1, settings)
    integrate_article(article2, settings)

    topic_file = tmp_path / "Second Brain" / "Second Brain - Development.md"
    content = topic_file.read_text()
    assert "### First Article" in content
    assert "### Second Article" in content
