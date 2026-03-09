# ABOUTME: Second Brain integrator that appends articles to centralized vault topic files.
# ABOUTME: Uses what/why table format, logs to Timeline, enables blog-writer and knowledge-sync signals.

import json
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select

from feed_brain.config import Settings, get_settings
from feed_brain.db.models import Article
from feed_brain.db.session import get_session_factory
from feed_brain.models import (
    CATEGORY_VAULT_MAP,
    ArticleStatus,
    Category,
    Tier,
)

log = structlog.get_logger()

TIMELINE_FILE = "Second Brain - Timeline"
SECOND_BRAIN_DIR = "Second Brain"


def _resolve_topic_file(category: str | None, vault_path: Path) -> tuple[str, Path]:
    """Resolve the topic filename and full path for a category.

    Returns (topic_filename, full_path) tuple.
    """
    try:
        cat = Category(category) if category else Category.DEVELOPMENT
    except ValueError:
        cat = Category.DEVELOPMENT

    topic_name = CATEGORY_VAULT_MAP.get(cat, "Second Brain - Development")
    topic_dir = vault_path / SECOND_BRAIN_DIR
    topic_dir.mkdir(parents=True, exist_ok=True)
    return topic_name, topic_dir / f"{topic_name}.md"


def _build_entry(article: Article) -> str:
    """Build a Second Brain entry in the what/why table format.

    Matches the content template used by process-clippings and newsletter-digest.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    title = article.title or "Untitled"

    # Use deep_summary if available (high-tier analyzed), otherwise triage summary
    description = article.deep_summary or article.summary or "No summary available."
    # Keep description concise: first 2 sentences max
    sentences = description.replace("\n", " ").split(". ")
    brief = ". ".join(sentences[:2]).strip()
    if not brief.endswith("."):
        brief += "."

    # What: core content. Why: relevance/benefit.
    what = brief
    why = article.reason or "Relevant to interests."

    parts = [f"### {title}", ""]
    parts.append(brief)
    parts.append("")
    parts.append("| Aspect | Detail |")
    parts.append("|--------|--------|")
    parts.append(f"| **What** | {what} |")
    parts.append(f"| **Why** | {why} |")

    # Add deep analysis extras if available
    if article.money_quote:
        parts.append("")
        parts.append(f'> "{article.money_quote}"')

    if article.deep_insights:
        insights = (
            json.loads(article.deep_insights) if isinstance(article.deep_insights, str) else []
        )
        if insights:
            parts.append("")
            for insight in insights[:3]:
                parts.append(f"- {insight}")

    if article.actionables:
        actionables = (
            json.loads(article.actionables) if isinstance(article.actionables, str) else []
        )
        if actionables:
            parts.append("")
            parts.append("**Actionable:**")
            for action in actionables:
                parts.append(f"- {action}")

    parts.append("")
    parts.append(f"- [Source]({article.url}) - {today}")
    parts.append("")

    return "\n".join(parts)


def _build_timeline_entry(article: Article, topic_name: str) -> str:
    """Build a Timeline log entry."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    title = article.title or "Untitled"
    return f"- **{today}** | [{title}] | Source: feed-brain | -> {topic_name}.md"


def _append_to_file(filepath: Path, content: str) -> bool:
    """Append content to a file, creating it if it doesn't exist."""
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with filepath.open("a", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError as e:
        log.error("file_append_error", path=str(filepath), error=str(e))
        return False


def integrate_article(article: Article, settings: Settings) -> str | None:
    """Integrate a single article into the Second Brain vault.

    Appends to the centralized topic file and logs to Timeline.
    Returns the topic filename on success, None on failure.
    """
    vault_path = settings.vault_path
    topic_name, topic_path = _resolve_topic_file(article.category, vault_path)

    # Build and append the entry
    entry = _build_entry(article)
    if not _append_to_file(topic_path, entry):
        return None

    # Log to Timeline
    timeline_path = vault_path / SECOND_BRAIN_DIR / f"{TIMELINE_FILE}.md"
    timeline_entry = _build_timeline_entry(article, topic_name)
    _append_to_file(timeline_path, timeline_entry + "\n")

    log.info("article_integrated", title=article.title, target=topic_name)
    return topic_name


async def integrate_articles() -> int:
    """Integrate high-tier articles into the Second Brain vault.

    Only integrates high-tier articles above auto_threshold confidence.
    Medium and low-tier articles are skipped entirely.

    Returns the number of articles integrated.
    """
    settings = get_settings()
    session_factory = get_session_factory()
    integrated = 0
    skipped = 0

    async with session_factory() as session:
        result = await session.execute(
            select(Article).where(
                Article.status.in_([ArticleStatus.ANALYZED, ArticleStatus.TRIAGED]),
                Article.tier.isnot(None),
                Article.integrated_at.is_(None),
            )
        )
        articles = result.scalars().all()

        for article in articles:
            confidence = article.confidence or 0.0
            tier = article.tier

            # Only integrate high-tier with sufficient confidence
            if tier != Tier.HIGH or confidence < settings.auto_threshold:
                skipped += 1
                continue

            target = integrate_article(article, settings)
            if target:
                article.integrated_at = datetime.now(UTC)
                article.integration_target = target
                article.status = ArticleStatus.INTEGRATED
                integrated += 1

        await session.commit()

    log.info("integration_complete", integrated=integrated, skipped=skipped, total=len(articles))
    return integrated
