# ABOUTME: Generates daily digest notes with top articles sorted by priority.
# ABOUTME: Translates content to Italian via local LLM, writes to vault Digests/ subfolder.

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ollama import AsyncClient

from feed_brain.config import get_settings
from feed_brain.db.models import Article
from feed_brain.db.session import get_session_factory
from feed_brain.models import ArticleStatus, Tier

log = structlog.get_logger()

TRANSLATE_PROMPT = """\
Traduci i seguenti campi in italiano. Rispondi SOLO con un oggetto JSON \
con le stesse chiavi e i valori tradotti. Non aggiungere o rimuovere chiavi. \
Se un valore e' gia' in italiano, restituiscilo invariato.\
"""

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


async def _translate_fields(
    fields: dict[str, str | list[str]],
    ollama_client: AsyncClient,
    model: str,
) -> dict[str, str | list[str]]:
    """Translate a dict of text fields to Italian using the local LLM.

    Returns translated dict on success, original dict on failure.
    """
    try:
        response = await ollama_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": TRANSLATE_PROMPT},
                {"role": "user", "content": json.dumps(fields, ensure_ascii=False)},
            ],
        )
        text = response.message.content.strip()
        return json.loads(text)
    except Exception as e:
        log.warning("translate_failed", error=str(e))
        return fields


async def _translate_article(
    article: Article, ollama_client: AsyncClient, model: str
) -> dict[str, str | list[str]]:
    """Extract and translate relevant fields from an article."""
    fields: dict[str, str | list[str]] = {}

    summary = article.deep_summary or article.summary
    if summary:
        fields["summary"] = summary
    if article.reason:
        fields["reason"] = article.reason
    if article.money_quote:
        fields["money_quote"] = article.money_quote
    if article.deep_insights:
        insights = (
            json.loads(article.deep_insights) if isinstance(article.deep_insights, str) else []
        )
        if insights:
            fields["insights"] = insights[:2]

    if not fields:
        return {}

    return await _translate_fields(fields, ollama_client, model)


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


def _build_article_section(
    article: Article, translations: dict[str, str | list[str]] | None = None
) -> str:
    """Build the main section for a high or medium tier article.

    Uses translated text from translations dict when available.
    """
    tr = translations or {}
    title = article.title or "Untitled"
    summary = tr.get("summary", article.deep_summary or article.summary or "No summary available.")
    reason = tr.get("reason", article.reason or "")
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
        money_quote = tr.get("money_quote", article.money_quote)
        parts.append(f"> {money_quote}")
        parts.append("")

    if article.tier == Tier.HIGH and article.deep_insights:
        insights = tr.get("insights")
        if insights is None:
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


def _build_digest(
    today: str,
    articles: list[Article],
    translations: dict[str, dict[str, str | list[str]]] | None = None,
) -> str:
    """Build the full digest markdown content.

    translations is a dict keyed by article URL mapping to translated fields.
    """
    tr_map = translations or {}
    sorted_articles = sorted(articles, key=_sort_key)

    high_medium = [a for a in sorted_articles if a.tier in (Tier.HIGH, Tier.MEDIUM)]
    low = [a for a in sorted_articles if a.tier == Tier.LOW]

    parts = [_build_frontmatter(today, sorted_articles)]
    parts.append(f"# Selezione - {today}")
    parts.append("")
    parts.append(DISCLAIMER)
    parts.append("")

    for article in high_medium:
        parts.append(_build_article_section(article, tr_map.get(article.url)))

    if low:
        parts.append(_build_low_tier_section(low))

    return "\n".join(parts)


async def generate_digest(
    session: AsyncSession | None = None,
    vault_path: Path | None = None,
    ollama_client: AsyncClient | None = None,
) -> int:
    """Generate the daily digest note in the vault.

    Queries for all articles classified today (UTC), excluding 'new' and 'error' status.
    Translates summaries, reasons, and insights to Italian via local LLM.
    Returns the count of articles included.
    """
    settings = get_settings()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    today_start = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC)
    tomorrow_start = today_start + timedelta(days=1)

    # Allow injecting dependencies for testing
    own_session = session is None
    if own_session:
        factory = get_session_factory()
        session = factory()

    if vault_path is None:
        vault_path = settings.vault_path

    if ollama_client is None:
        from ollama import AsyncClient

        ollama_client = AsyncClient(host=settings.ollama_host)

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

        # Translate article fields to Italian
        translations: dict[str, dict[str, str | list[str]]] = {}
        for article in articles:
            if article.tier == Tier.LOW:
                continue  # low-tier only shows title+link, no text to translate
            tr = await _translate_article(article, ollama_client, settings.ollama_model)
            if tr:
                translations[article.url] = tr

        log.info("digest_translations_done", translated=len(translations))

        content = _build_digest(today, articles, translations)

        digest_dir = vault_path / "Digests"
        digest_dir.mkdir(parents=True, exist_ok=True)
        digest_file = digest_dir / f"Selezione - {today}.md"
        digest_file.write_text(content, encoding="utf-8")

        log.info("digest_generated", file=str(digest_file), articles=len(articles))
        return len(articles)
    finally:
        if own_session:
            await session.close()
