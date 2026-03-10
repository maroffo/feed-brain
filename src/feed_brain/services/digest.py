# ABOUTME: Generates daily digest notes with top articles sorted by priority.
# ABOUTME: Writes Selezione - YYYY-MM-DD.md to vault Digests/ subfolder with YAML frontmatter.

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from feed_brain.config import get_settings
from feed_brain.db.models import Article
from feed_brain.db.session import get_session_factory
from feed_brain.models import ArticleStatus, Tier

log = structlog.get_logger()

# Priority ordering for tiers (lower value = higher priority)
_TIER_ORDER = {Tier.HIGH: 0, Tier.MEDIUM: 1, Tier.LOW: 2}

DISCLAIMER = (
    "> Nota generata automaticamente da **feed-brain**. "
    "Contenuti selezionati e ordinati per rilevanza.\n"
)


def _sort_key(article: Article) -> tuple[int, float]:
    """Sort key: tier priority ascending, then confidence descending."""
    tier_rank = _TIER_ORDER.get(article.tier, 99)
    confidence = -(article.confidence or 0.0)
    return (tier_rank, confidence)


def _build_frontmatter(today: str, articles: list[Article]) -> str:
    """Build YAML frontmatter block."""
    high_count = sum(1 for a in articles if a.tier == Tier.HIGH)
    medium_count = sum(1 for a in articles if a.tier == Tier.MEDIUM)
    low_count = sum(1 for a in articles if a.tier == Tier.LOW)

    lines = [
        "---",
        f"date: {today}",
        "type: daily-digest",
        f"high_count: {high_count}",
        f"medium_count: {medium_count}",
        f"low_count: {low_count}",
        f"total: {len(articles)}",
        "---",
        "",
    ]
    return "\n".join(lines)


def _build_article_section(article: Article) -> str:
    """Build the main section for a high or medium tier article."""
    title = article.title or "Untitled"
    summary = article.deep_summary or article.summary or "No summary available."
    reason = article.reason or ""
    tier_label = (article.tier or "?").upper()
    category_label = (article.category or "uncategorized").replace("_", " ")

    parts = [
        f"### {title}",
        "",
        summary,
        "",
    ]

    if reason:
        parts.append(f"**Perche:** {reason}")
        parts.append("")

    parts.append(f"`{tier_label}` `{category_label}`  [Leggi]({article.url})")
    parts.append("")

    # Deep analysis extras for high-tier analyzed articles
    if article.tier == Tier.HIGH and article.money_quote:
        parts.append(f"> {article.money_quote}")
        parts.append("")

    if article.tier == Tier.HIGH and article.deep_insights:
        insights = (
            json.loads(article.deep_insights) if isinstance(article.deep_insights, str) else []
        )
        for insight in insights[:2]:
            parts.append(f"- {insight}")
        if insights:
            parts.append("")

    return "\n".join(parts)


def _build_low_tier_section(articles: list[Article]) -> str:
    """Build the collapsible callout section for low-tier articles."""
    count = len(articles)
    lines = [
        f"> [!info]- Filtrati ({count} articoli)",
    ]
    for article in articles:
        title = article.title or "Untitled"
        lines.append(f"> - [{title}]({article.url})")

    lines.append("")
    return "\n".join(lines)


def _build_digest(today: str, articles: list[Article]) -> str:
    """Build the full digest markdown content."""
    sorted_articles = sorted(articles, key=_sort_key)

    high_medium = [a for a in sorted_articles if a.tier in (Tier.HIGH, Tier.MEDIUM)]
    low = [a for a in sorted_articles if a.tier == Tier.LOW]

    parts = [_build_frontmatter(today, sorted_articles)]
    parts.append(f"# Selezione - {today}")
    parts.append("")
    parts.append(DISCLAIMER)
    parts.append("")

    for article in high_medium:
        parts.append(_build_article_section(article))

    if low:
        parts.append(_build_low_tier_section(low))

    return "\n".join(parts)


async def generate_digest(
    session: AsyncSession | None = None,
    vault_path: Path | None = None,
) -> int:
    """Generate the daily digest note in the vault.

    Queries for all articles classified today (UTC), excluding 'new' and 'error' status.
    Returns the count of articles included.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    today_start = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC)
    tomorrow_start = today_start + timedelta(days=1)

    # Allow injecting session and vault_path for testing
    own_session = session is None
    if own_session:
        factory = get_session_factory()
        session = factory()

    if vault_path is None:
        vault_path = get_settings().vault_path

    try:
        result = await session.execute(
            select(Article).where(
                Article.classified_at >= today_start,
                Article.classified_at < tomorrow_start,
                Article.status.notin_([ArticleStatus.NEW, ArticleStatus.ERROR]),
            )
        )
        articles = list(result.scalars().all())

        if not articles:
            log.info("digest_skipped", reason="no_articles_today")
            return 0

        content = _build_digest(today, articles)

        digest_dir = vault_path / "Digests"
        digest_dir.mkdir(parents=True, exist_ok=True)
        digest_file = digest_dir / f"Selezione - {today}.md"
        digest_file.write_text(content, encoding="utf-8")

        log.info("digest_generated", file=str(digest_file), articles=len(articles))
        return len(articles)
    finally:
        if own_session:
            await session.close()
