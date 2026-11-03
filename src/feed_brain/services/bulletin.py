# ABOUTME: Daily bulletin pipeline: candidates → per-section clusters → ranking → Opus on top leads → Gemini writer.
# ABOUTME: Idempotent per issue_date (force regenerates); every candidate is stamped so it is never covered twice.

import asyncio
import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import structlog
from anthropic import AsyncAnthropic
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from feed_brain.ai.gemini import GeminiError
from feed_brain.ai.prompts import BULLETIN_PROMPT
from feed_brain.config import Settings, get_settings
from feed_brain.db.models import EMAIL_FEED_TYPE, Article, Bulletin, FeedSource
from feed_brain.db.session import get_session_factory
from feed_brain.models import (
    SECTION_TITLES,
    ArticleStatus,
    BriefItem,
    BulletinData,
    BulletinDraft,
    DeepAnalysis,
    RadarItem,
    Section,
    SectionBlock,
    SourceRef,
    Tier,
    TopStory,
)
from feed_brain.services.analyzer import deep_analyze
from feed_brain.services.clustering import cluster_candidates
from feed_brain.services.ranking import THREAT_RADAR, Candidate, Story, radar_items, select_top
from feed_brain.textutil import clean_text

log = structlog.get_logger()

_MONTHS = [
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
]


class BulletinLLM(Protocol):
    """Gemini surface used here: structured generate (clustering, writer) and text (Minions)."""

    async def generate[T: BaseModel](self, prompt: str, system: str, schema: type[T]) -> T: ...

    async def generate_text(self, prompt: str, system: str) -> str: ...


def today_rome() -> date:
    """Issue dates follow the reader's calendar, not UTC."""
    return datetime.now(ZoneInfo("Europe/Rome")).date()


def italian_date(day: date) -> str:
    """'2 novembre 2026'."""
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def short_source_name(name: str | None) -> str:
    """Outlet name without the feed's tagline ('Over Security - Cybersecurity news' → 'Over Security')."""
    if not name:
        return "Fonte sconosciuta"
    for separator in (" - ", " | ", " – ", ", "):
        name = name.split(separator, 1)[0]
    return name.strip() or "Fonte sconosciuta"


def first_sentence(text: str) -> str:
    """First sentence of a summary, always ending with a period (empty stays empty)."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    head = text.split(". ", 1)[0].rstrip(".")
    return f"{head}."


def _sources(story: Story) -> list[SourceRef]:
    return [SourceRef(name=c.source, title=c.title, url=c.url, paid=c.paid) for c in story.articles]


def assemble(
    issue_date: date,
    draft: BulletinDraft | None,
    top: list[Story],
    rest: list[Story],
    radar: list[Candidate],
    analyses: dict[int, DeepAnalysis],
    articles_count: int,
    section_cap: int = 6,
) -> BulletinData:
    """Pure assembly of the issue; writer gaps fall back to cluster headline + lead summary."""
    written = {w.index: w for w in draft.top_stories} if draft else {}
    top_stories = []
    for index, story in enumerate(top):
        w = written.get(index)
        top_stories.append(
            TopStory(
                title=(clean_text(w.title).strip() if w else "") or story.headline,
                text=(clean_text(w.text).strip() if w else "") or story.lead.summary,
                section=Section(story.section),
                sources=_sources(story),
                analysis=analyses.get(story.lead.article_id),
                lead_url=story.lead.url,
            )
        )

    by_section: dict[str, list[Story]] = defaultdict(list)
    for story in rest:
        by_section[story.section].append(story)

    def shown(stories: list[Story]) -> list[Story]:
        """The first section_cap stories, plus every one built on a forwarded newsletter
        (picked by hand, so never folded into "e altre N storie")."""
        return [
            s
            for rank, s in enumerate(stories)
            if rank < section_cap or any(c.paid for c in s.articles)
        ]

    sections = []
    for section in Section:
        stories = by_section.get(section.value)
        if section == Section.THREAT_RADAR or not stories:
            continue
        listed = shown(stories)
        sections.append(
            SectionBlock(
                key=section,
                title=SECTION_TITLES[section],
                items=[
                    BriefItem(
                        headline=s.headline,
                        summary=first_sentence(s.lead.summary),
                        sources=_sources(s),
                    )
                    for s in listed
                ],
                omitted=len(stories) - len(listed),
            )
        )

    return BulletinData(
        issue_date=issue_date,
        title=(clean_text(draft.title).strip() if draft else "")
        or f"Il bollettino del {italian_date(issue_date)}",
        subtitle=clean_text(draft.subtitle) if draft else "",
        editorial=clean_text(draft.editorial) if draft else "",
        top_stories=top_stories,
        sections=sections,
        radar=[RadarItem(title=c.title, url=c.url, source=c.source) for c in radar],
        articles_count=articles_count,
    )


async def _load_candidates(
    session: AsyncSession, window_hours: int, regenerating: int | None = None
) -> list[Candidate]:
    """Unstamped recent articles, plus (on --force) the articles of the issue being replaced."""
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    fresh = and_(Article.bulletin_id.is_(None), Article.fetched_at >= cutoff)
    scope = or_(fresh, Article.bulletin_id == regenerating) if regenerating is not None else fresh
    rows = await session.execute(
        select(
            Article.id,
            Article.title,
            Article.url,
            FeedSource.name,
            Article.tier,
            Article.section,
            Article.confidence,
            Article.summary,
            func.coalesce(func.length(Article.content), 0),
            FeedSource.feed_type,
        )
        .outerjoin(FeedSource, Article.source_id == FeedSource.id)
        .where(
            Article.status.in_(
                [ArticleStatus.TRIAGED, ArticleStatus.ANALYZED, ArticleStatus.INTEGRATED]
            ),
            scope,
            Article.section.isnot(None),
            or_(
                Article.tier.in_([Tier.HIGH, Tier.MEDIUM]),
                Article.section == THREAT_RADAR,
                # Forwarded newsletters were picked by hand: the tier only ranks them.
                FeedSource.feed_type == EMAIL_FEED_TYPE,
            ),
        )
        .order_by(Article.id)
    )
    return [
        Candidate(
            article_id=article_id,
            title=title,
            url=url,
            source=short_source_name(source),
            tier=tier or Tier.LOW,
            section=section or "",
            confidence=confidence or 0.0,
            summary=summary or "",
            content_len=content_len,
            paid=feed_type == EMAIL_FEED_TYPE,
        )
        for (
            article_id,
            title,
            url,
            source,
            tier,
            section,
            confidence,
            summary,
            content_len,
            feed_type,
        ) in rows
    ]


def _stored_analysis(article: Article) -> DeepAnalysis | None:
    if not article.deep_summary:
        return None
    return DeepAnalysis(
        summary=article.deep_summary,
        insights=json.loads(article.deep_insights or "[]"),
        money_quote=article.money_quote or "",
        actionables=json.loads(article.actionables or "[]"),
    )


async def _analyze_leads(
    leads: list[Article],
    anthropic: AsyncAnthropic | None,
    gemini: BulletinLLM,
    settings: Settings,
) -> tuple[dict[int, DeepAnalysis], dict[int, DeepAnalysis]]:
    """Returns (all analyses by article id, the subset that is new and must be stored)."""
    analyses: dict[int, DeepAnalysis] = {}
    pending: list[Article] = []
    for article in leads:
        stored = _stored_analysis(article)
        if stored:
            analyses[article.id] = stored
        else:
            pending.append(article)

    if anthropic is None or not pending:
        return analyses, {}

    semaphore = asyncio.Semaphore(settings.analysis_concurrency)

    async def _bounded(article: Article) -> tuple[int, DeepAnalysis | None]:
        async with semaphore:
            return article.id, await deep_analyze(article, anthropic, gemini, settings)

    fresh = {
        article_id: analysis
        for article_id, analysis in await asyncio.gather(*(_bounded(a) for a in pending))
        if analysis is not None
    }
    analyses.update(fresh)
    return analyses, fresh


def _writer_prompt(issue_date: date, top: list[Story], analyses: dict[int, DeepAnalysis]) -> str:
    blocks = [f"Data: {italian_date(issue_date)}"]
    for index, story in enumerate(top):
        lines = [f"[{index}] Sezione: {SECTION_TITLES[Section(story.section)]}"]
        lines.append(f"Titolo di lavoro: {story.headline}")
        lines += [f'- {c.source}: "{c.title}". {c.summary}' for c in story.articles]
        analysis = analyses.get(story.lead.article_id)
        if analysis:
            lines.append(f"Analisi: {analysis.summary}")
            lines += [f"  * {insight}" for insight in analysis.insights]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def _write(
    issue_date: date, top: list[Story], analyses: dict[int, DeepAnalysis], gemini: BulletinLLM
) -> BulletinDraft | None:
    if not top:
        return None
    try:
        draft = await gemini.generate(
            _writer_prompt(issue_date, top, analyses), BULLETIN_PROMPT, BulletinDraft
        )
    except GeminiError as e:
        log.warning("bulletin_writer_failed", error=str(e))
        return None
    return draft


async def generate_bulletin(
    issue_date: date,
    *,
    force: bool = False,
    gemini: BulletinLLM | None = None,
    anthropic: AsyncAnthropic | None = None,
    settings: Settings | None = None,
) -> Bulletin | None:
    """Build and store the issue for `issue_date`. Returns None when there is nothing to cover."""
    settings = settings or get_settings()
    factory = get_session_factory()

    async with factory() as session:
        existing = (
            await session.execute(select(Bulletin).where(Bulletin.issue_date == issue_date))
        ).scalar_one_or_none()
        if existing is not None and not force:
            log.info("bulletin_exists", issue_date=str(issue_date), bulletin_id=existing.id)
            return existing
        candidates = await _load_candidates(
            session, settings.candidate_window_hours, existing.id if existing else None
        )

    if not candidates:
        log.warning("bulletin_no_candidates", issue_date=str(issue_date))
        return existing

    if gemini is None:
        from feed_brain.ai.gemini import Gemini

        gemini = Gemini(settings)

    stories = await cluster_candidates(candidates, gemini, settings.triage_concurrency)
    top, rest = select_top(stories, settings.top_stories)
    radar = radar_items([c for c in candidates if c.section == THREAT_RADAR], settings.radar_cap)

    async with factory() as session:
        lead_ids = [story.lead.article_id for story in top]
        leads = list(
            (await session.execute(select(Article).where(Article.id.in_(lead_ids)))).scalars()
        )
    analyses, fresh = await _analyze_leads(leads, anthropic, gemini, settings)

    draft = await _write(issue_date, top, analyses, gemini)
    data = assemble(
        issue_date, draft, top, rest, radar, analyses, len(candidates), settings.section_cap
    )

    async with factory() as session:
        # Replace the old issue in the same transaction as the new one: a failed
        # regeneration above leaves the previous bulletin untouched.
        if existing is not None:
            await session.execute(
                update(Article).where(Article.bulletin_id == existing.id).values(bulletin_id=None)
            )
            await session.execute(delete(Bulletin).where(Bulletin.id == existing.id))
        bulletin = Bulletin(
            issue_date=issue_date,
            title=data.title,
            subtitle=data.subtitle,
            editorial=data.editorial,
            content_json=data.model_dump_json(),
            articles_count=data.articles_count,
        )
        session.add(bulletin)
        try:
            await session.flush()
        except IntegrityError:
            # A concurrent run stored this issue first: keep theirs.
            await session.rollback()
            winner = (
                await session.execute(select(Bulletin).where(Bulletin.issue_date == issue_date))
            ).scalar_one()
            log.warning("bulletin_concurrent_insert", issue_date=str(issue_date))
            return winner
        await session.execute(
            update(Article)
            .where(Article.id.in_([c.article_id for c in candidates]))
            .values(bulletin_id=bulletin.id)
        )
        for article_id, analysis in fresh.items():
            await session.execute(
                update(Article)
                .where(Article.id == article_id)
                .values(
                    deep_summary=analysis.summary,
                    deep_insights=json.dumps(analysis.insights, ensure_ascii=False),
                    money_quote=analysis.money_quote,
                    actionables=json.dumps(analysis.actionables, ensure_ascii=False),
                    status=ArticleStatus.ANALYZED,
                )
            )
        await session.commit()

    log.info(
        "bulletin_generated",
        issue_date=str(issue_date),
        candidates=len(candidates),
        stories=len(stories),
        top=len(top),
        analyzed=len(fresh),
        radar=len(radar),
    )
    return bulletin
