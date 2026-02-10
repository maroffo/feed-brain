# ABOUTME: Obsidian clipping generator for approved articles.
# ABOUTME: Creates markdown files in the Clippings/ folder matching existing format.

import json
from datetime import UTC, datetime
from pathlib import Path

import structlog

from feed_brain.config import get_settings
from feed_brain.db.models import Article

log = structlog.get_logger()

CLIPPING_TEMPLATE = """\
---
title: {title}
source: {url}
author:
  - {author}
published: {published}
created: {created}
description: {summary}
tags:
  - "clippings"
  - "feed-brain"
---

{ai_section}{content}
"""


def _build_ai_section(article: Article) -> str:
    """Build markdown AI analysis section from article fields."""
    parts = []

    if article.summary:
        parts.append(f"## AI Summary\n\n{article.summary}")

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

    return "\n\n".join(parts) + "\n\n---\n\n"


def _sanitize_filename(title: str) -> str:
    """Remove characters invalid in filenames."""
    invalid = '<>:"/\\|?*'
    result = title
    for ch in invalid:
        result = result.replace(ch, "")
    return result.strip()[:200]


async def create_clipping(article: Article, clippings_dir: Path | None = None) -> bool:
    """Create a markdown clipping file for an approved article.

    Returns True if the file was created successfully.
    """
    settings = get_settings()
    target_dir = clippings_dir or settings.clippings_dir

    if not target_dir.exists():
        log.error("clippings_dir_not_found", path=str(target_dir))
        return False

    filename = _sanitize_filename(article.title) + ".md"
    filepath = target_dir / filename

    if filepath.exists():
        log.warning("clipping_already_exists", path=str(filepath))
        return True

    published = ""
    if article.published_date:
        published = article.published_date.strftime("%Y-%m-%d")

    def _yaml_str(value: str) -> str:
        """Safely quote a string for YAML frontmatter using JSON encoding."""
        return json.dumps(value)

    content = CLIPPING_TEMPLATE.format(
        title=_yaml_str(article.title),
        url=_yaml_str(article.url),
        author=_yaml_str(article.author or "Unknown"),
        published=published,
        created=datetime.now(UTC).strftime("%Y-%m-%d"),
        summary=_yaml_str(article.summary or ""),
        ai_section=_build_ai_section(article),
        content=article.content or "",
    )

    try:
        filepath.write_text(content, encoding="utf-8")
        log.info("clipping_created", path=str(filepath))
        return True
    except OSError as e:
        log.error("clipping_write_error", path=str(filepath), error=str(e))
        return False
