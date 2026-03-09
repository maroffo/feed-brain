# ABOUTME: Second Brain integrator that writes articles to Obsidian vault.
# ABOUTME: Routes articles to category folders, handles auto/interactive/skip logic.

import json
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select

from feed_brain.config import get_settings
from feed_brain.db.models import Article
from feed_brain.db.session import get_session_factory
from feed_brain.models import (
    CATEGORY_VAULT_MAP,
    ArticleStatus,
    Category,
    Tier,
)

log = structlog.get_logger()

NOTE_TEMPLATE = """\
---
title: {title}
source: "{url}"
author: "{author}"
published: {published}
created: {created}
tier: {tier}
category: {category}
confidence: {confidence}
tags:
  - feed-brain
  - {category}
---

## Summary

{summary}
{deep_section}
---

> Source: [{title}]({url})
"""


def _build_deep_section(article: Article) -> str:
    """Build the deep analysis section if available."""
    parts = []

    if article.deep_summary:
        parts.append(f"## Deep Analysis\n\n{article.deep_summary}")

    if article.deep_insights:
        insights = (
            json.loads(article.deep_insights) if isinstance(article.deep_insights, str) else []
        )
        if insights:
            items = "\n".join(f"- {item}" for item in insights)
            parts.append(f"## Insights\n\n{items}")

    if article.money_quote:
        parts.append(f'## Money Quote\n\n> "{article.money_quote}"')

    if article.actionables:
        actionables = (
            json.loads(article.actionables) if isinstance(article.actionables, str) else []
        )
        if actionables:
            items = "\n".join(f"- {item}" for item in actionables)
            parts.append(f"## Actionable Takeaways\n\n{items}")

    if not parts:
        return ""

    return "\n\n" + "\n\n".join(parts) + "\n\n"


def _sanitize_filename(title: str) -> str:
    """Remove characters invalid in filenames."""
    invalid = '<>:"/\\|?*'
    result = title
    for ch in invalid:
        result = result.replace(ch, "")
    return result.strip()[:200]


def _write_note(article: Article, vault_path: Path) -> str | None:
    """Write a single article as a markdown note to the vault.

    Returns the file path relative to vault, or None on failure.
    """
    try:
        category = Category(article.category) if article.category else Category.DEVELOPMENT
    except ValueError:
        category = Category.DEVELOPMENT
    subfolder = CATEGORY_VAULT_MAP.get(category, "Resources/Uncategorized")
    target_dir = vault_path / subfolder
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = _sanitize_filename(article.title) + ".md"
    filepath = target_dir / filename

    if filepath.exists():
        log.warning("note_already_exists", path=str(filepath))
        return str(filepath.relative_to(vault_path))

    published = ""
    if article.published_date:
        published = article.published_date.strftime("%Y-%m-%d")

    content = NOTE_TEMPLATE.format(
        title=article.title.replace('"', '\\"'),
        url=article.url,
        author=article.author or "Unknown",
        published=published,
        created=datetime.now(UTC).strftime("%Y-%m-%d"),
        tier=article.tier or "unknown",
        category=article.category or "uncategorized",
        confidence=article.confidence or 0.0,
        summary=article.summary or "No summary available.",
        deep_section=_build_deep_section(article),
    )

    try:
        filepath.write_text(content, encoding="utf-8")
        log.info("note_created", path=str(filepath))
        return str(filepath.relative_to(vault_path))
    except OSError as e:
        log.error("note_write_error", path=str(filepath), error=str(e))
        return None


async def integrate_articles(auto: bool = False) -> int:
    """Integrate articles into the Second Brain vault.

    If auto=True, integrates all articles above auto_threshold without prompting.
    If auto=False, prompts for medium-confidence articles.

    Returns the number of articles integrated.
    """
    settings = get_settings()
    session_factory = get_session_factory()
    integrated = 0

    async with session_factory() as session:
        # Get articles ready for integration (analyzed high-tier + triaged medium/low)
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

            # Skip low-tier and below interactive threshold
            if tier == Tier.LOW or confidence < settings.interactive_threshold:
                continue

            # Auto-integrate high confidence + high tier
            if auto or (confidence >= settings.auto_threshold and tier == Tier.HIGH):
                target = _write_note(article, settings.vault_path)
                if target:
                    article.integrated_at = datetime.now(UTC)
                    article.integration_target = target
                    article.status = ArticleStatus.INTEGRATED
                    integrated += 1
                continue

            # Interactive: prompt for medium confidence or medium tier
            print(f"\n{'=' * 60}")
            print(f"  {article.title}")
            print(f"  Tier: {tier} | Category: {article.category} | Confidence: {confidence:.2f}")
            print(f"  {article.summary or 'No summary'}")
            print(f"{'=' * 60}")

            response = input("  Integrate? [y/n/q] ").strip().lower()
            if response == "q":
                break
            if response == "y":
                target = _write_note(article, settings.vault_path)
                if target:
                    article.integrated_at = datetime.now(UTC)
                    article.integration_target = target
                    article.status = ArticleStatus.INTEGRATED
                    integrated += 1

        await session.commit()

    log.info("integration_complete", integrated=integrated, total=len(articles))
    return integrated
