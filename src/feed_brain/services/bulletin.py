# ABOUTME: Bulletin pipeline per edition: candidates → per-section clusters → ranking → Opus (notes, essays) → writer.
# ABOUTME: Idempotent per (issue_date, edition), force regenerates; every candidate is stamped so it is covered once.

import asyncio
import json
import re
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
    DAILY_EDITION,
    EDITIONS,
    SECTION_TITLES,
    ArticleStatus,
    BriefItem,
    BulletinData,
    BulletinDraft,
    DeepAnalysis,
    Edition,
    Section,
    SectionBlock,
    SourceRef,
    StoryEssay,
    Tier,
    TopStory,
)
from feed_brain.services.analyzer import deep_analyze
from feed_brain.services.clustering import cluster_candidates
from feed_brain.services.essay import write_essays
from feed_brain.services.ranking import Candidate, Story, select_top
from feed_brain.textutil import clean_text

log = structlog.get_logger()

SHOWN_NOTES = 2  # insights and to-dos shown per analyzed story; the article row keeps them all

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
    for separator in (" - ", " | ", " – ", " > ", ", "):
        name = name.split(separator, 1)[0]
    return name.strip() or "Fonte sconosciuta"


# A period after these does not end the sentence ("n. 123", "dott. Rossi").
_ABBREVIATIONS = frozenset(
    {"n", "nr", "art", "artt", "co", "dott", "dr", "prof", "sig", "on", "sen", "avv", "ing"}
    | {"mr", "mrs", "ms", "st", "vs", "ecc", "etc", "pag", "cap", "fig", "vol"}
)
_SENTENCE_END = re.compile(r"(\S+)([.!?])\s+(?=[\"'«“(]?[A-ZÀ-Þ])")
_DOTTED = re.compile(r"(?:[^\W\d_]\.)+[^\W\d_]")  # "u.s", "s.p.a": letters joined by periods


def first_sentence(text: str) -> str:
    """First sentence of a summary, ending with its own punctuation (empty stays empty).

    A sentence ends at ".", "?" or "!" before a capital letter; a period never ends it after an
    initial ("J."), a known abbreviation ("n.", "dott.") or an acronym written with periods
    ("U.S.", "S.p.A."), even behind an elided article ("l'on.", "dell’avv.").
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    for match in _SENTENCE_END.finditer(text):
        if match.group(2) != ".":
            return text[: match.end(2)]
        word = re.split(r"['’]", match.group(1).lstrip("(\"'«“‘").lower())[-1]
        initial = len(word) == 1 and word.isalpha()
        if not initial and word not in _ABBREVIATIONS and not _DOTTED.fullmatch(word):
            return text[: match.end(2)]
    return text if text[-1] in ".!?" else f"{text}."


def _sources(story: Story) -> list[SourceRef]:
    return [SourceRef(name=c.source, title=c.title, url=c.url, paid=c.paid) for c in story.articles]


def _area(section: str, areas: tuple[tuple[Section, ...], ...]) -> int:
    """Reading position of a section's area; sections outside the edition go last."""
    return next((i for i, area in enumerate(areas) if section in area), len(areas))


def shown_analysis(analysis: DeepAnalysis | None) -> DeepAnalysis | None:
    """What the issue shows of an analysis: the quote always; insights and to-dos only when
    the story is actionable for the reader (the analysis has to-dos), SHOWN_NOTES each."""
    if analysis is None:
        return None
    actionables = analysis.actionables[:SHOWN_NOTES]
    insights = analysis.insights[:SHOWN_NOTES] if actionables else []
    return analysis.model_copy(update={"insights": insights, "actionables": actionables})


def assemble(
    issue_date: date,
    draft: BulletinDraft | None,
    top: list[Story],
    rest: list[Story],
    analyses: dict[int, DeepAnalysis],
    articles_count: int,
    section_cap: int = 3,
    *,
    essays: dict[int, StoryEssay] | None = None,
    edition: Edition | None = None,
) -> BulletinData:
    """Pure assembly of the issue; writer gaps fall back to cluster headline + lead summary.

    Top stories arrive by importance and are shown grouped by the edition's areas, keeping
    the importance order inside each area; 'In breve' follows the edition's sections.
    """
    edition = edition or EDITIONS[DAILY_EDITION]
    written = {w.index: w for w in draft.top_stories} if draft else {}
    top_stories = []
    # sorted() is stable: inside an area the stories keep their rank.
    for index, story in sorted(
        enumerate(top), key=lambda pair: _area(pair[1].section, edition.areas)
    ):
        w = written.get(index)
        top_stories.append(
            TopStory(
                title=(clean_text(w.title).strip() if w else "") or story.headline,
                text=(clean_text(w.text).strip() if w else "") or story.lead.summary,
                section=Section(story.section),
                sources=_sources(story),
                analysis=shown_analysis(analyses.get(story.lead.article_id)),
                lead_url=story.lead.url,
                essay=(essays or {}).get(story.lead.article_id),
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
    for section in edition.sections:
        stories = by_section.get(section.value)
        if not stories:
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

    key_points = [clean_text(p).strip() for p in draft.key_points] if draft else []
    fallback = "Il bollettino" if edition.is_daily else edition.title
    return BulletinData(
        issue_date=issue_date,
        edition=edition.key,
        title=(clean_text(draft.title).strip() if draft else "")
        or f"{fallback} del {italian_date(issue_date)}",
        key_points=[p for p in key_points if p],
        top_stories=top_stories,
        sections=sections,
        articles_count=articles_count,
    )


async def _load_candidates(
    session: AsyncSession,
    window_hours: int,
    sections: tuple[Section, ...],
    regenerating: int | None = None,
) -> list[Candidate]:
    """Unstamped recent articles of the edition's sections, plus (on --force) the articles of
    the issue being replaced. Other sections stay unstamped for their own edition."""
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
            Article.section.in_([section.value for section in sections]),
            or_(
                Article.tier.in_([Tier.HIGH, Tier.MEDIUM]),
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
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[dict[int, DeepAnalysis], dict[int, DeepAnalysis]]:
    """Returns (all analyses by article id, the subset that is new and must be stored).
    `semaphore` bounds concurrent Opus calls; generate_bulletin shares it with the essays."""
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

    slots = semaphore or asyncio.Semaphore(settings.analysis_concurrency)

    async def _bounded(article: Article) -> tuple[int, DeepAnalysis | None]:
        async with slots:
            return article.id, await deep_analyze(article, anthropic, gemini, settings)

    fresh = {
        article_id: analysis
        for article_id, analysis in await asyncio.gather(*(_bounded(a) for a in pending))
        if analysis is not None
    }
    analyses.update(fresh)
    return analyses, fresh


def _writer_prompt(
    issue_date: date, top: list[Story], analyses: dict[int, DeepAnalysis], edition: Edition
) -> str:
    blocks = [f"Data: {italian_date(issue_date)}"]
    if not edition.is_daily:
        blocks[0] += f"\nEdizione: {edition.title} (solo storie di questo tema)"
    for index, story in enumerate(top):
        lines = [f"[{index}] Sezione: {SECTION_TITLES[Section(story.section)]}"]
        lines.append(f"Titolo di lavoro: {story.headline}")
        lines += [f'- {c.source}: "{c.title}". {c.summary}' for c in story.articles]
        analysis = analyses.get(story.lead.article_id)
        if analysis:
            lines.append(f"Analisi: {analysis.summary}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def _write(
    issue_date: date,
    top: list[Story],
    analyses: dict[int, DeepAnalysis],
    gemini: BulletinLLM,
    edition: Edition,
) -> BulletinDraft | None:
    if not top:
        return None
    try:
        draft = await gemini.generate(
            _writer_prompt(issue_date, top, analyses, edition), BULLETIN_PROMPT, BulletinDraft
        )
    except GeminiError as e:
        log.warning("bulletin_writer_failed", error=str(e))
        return None
    return draft


async def generate_bulletin(
    issue_date: date,
    *,
    edition: Edition | None = None,
    force: bool = False,
    gemini: BulletinLLM | None = None,
    anthropic: AsyncAnthropic | None = None,
    settings: Settings | None = None,
) -> Bulletin | None:
    """Build and store the edition's issue for `issue_date` (default: the main issue).
    Returns None when there is nothing to cover: no row, so nothing is sent."""
    settings = settings or get_settings()
    edition = edition or EDITIONS[DAILY_EDITION]
    factory = get_session_factory()
    this_issue = (Bulletin.issue_date == issue_date, Bulletin.edition == edition.key)

    async with factory() as session:
        existing = (await session.execute(select(Bulletin).where(*this_issue))).scalar_one_or_none()
        if existing is not None and not force:
            log.info(
                "bulletin_exists",
                issue_date=str(issue_date),
                edition=edition.key,
                bulletin_id=existing.id,
            )
            return existing
        candidates = await _load_candidates(
            session,
            settings.candidate_window_hours,
            edition.sections,
            existing.id if existing else None,
        )

    if not candidates:
        log.warning("bulletin_no_candidates", issue_date=str(issue_date), edition=edition.key)
        return existing

    if gemini is None:
        from feed_brain.ai.gemini import Gemini

        gemini = Gemini(settings)

    stories = await cluster_candidates(candidates, gemini, settings.triage_concurrency)
    top, rest = select_top(stories, edition.top_stories or settings.top_stories)

    # Essays read every article of every top story; lead notes only the first leads.
    async with factory() as session:
        top_ids = [c.article_id for story in top for c in story.articles]
        rows = {
            a.id: a
            for a in (
                await session.execute(select(Article).where(Article.id.in_(top_ids)))
            ).scalars()
        }
    lead_ids = [
        story.lead.article_id
        for story in top[: settings.analysis_top_n]
        if story.lead.content_len >= settings.analysis_min_chars
    ]
    leads = [rows[i] for i in lead_ids if i in rows]
    opus_slots = asyncio.Semaphore(settings.analysis_concurrency)  # one budget for both
    (analyses, fresh), essays = await asyncio.gather(
        _analyze_leads(leads, anthropic, gemini, settings, opus_slots),
        write_essays(
            [s for s in top if len(s.articles) >= edition.essay_min_sources],
            rows,
            anthropic,
            settings,
            opus_slots,
        ),
    )

    draft = await _write(issue_date, top, analyses, gemini, edition)
    data = assemble(
        issue_date,
        draft,
        top,
        rest,
        analyses,
        len(candidates),
        settings.section_cap,
        essays=essays,
        edition=edition,
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
            edition=edition.key,
            title=data.title,
            subtitle=data.subtitle,
            editorial="\n".join(data.key_points),
            content_json=data.model_dump_json(),
            articles_count=data.articles_count,
        )
        session.add(bulletin)
        try:
            await session.flush()
        except IntegrityError:
            # A concurrent run stored this issue first: keep theirs.
            await session.rollback()
            winner = (await session.execute(select(Bulletin).where(*this_issue))).scalar_one()
            log.warning(
                "bulletin_concurrent_insert", issue_date=str(issue_date), edition=edition.key
            )
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
    )
    return bulletin
