# ABOUTME: Sunday weekly recap synthesized by Gemini from the week's stored bulletins (Mon-Sun).
# ABOUTME: Due on Sunday, caught up Mon-Sat if last week's recap is missing; idempotent per week_start.

from datetime import date, timedelta
from typing import Protocol

import structlog
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from feed_brain.ai.gemini import GeminiError
from feed_brain.ai.prompts import WEEKLY_PROMPT
from feed_brain.config import Settings, get_settings
from feed_brain.db.models import Bulletin, WeeklyDigest
from feed_brain.db.session import get_session_factory
from feed_brain.models import (
    DAILY_EDITION,
    BulletinData,
    BulletinRef,
    SourceRef,
    WeeklyArcData,
    WeeklyData,
    WeeklyDraft,
)
from feed_brain.services.bulletin import italian_date
from feed_brain.textutil import clean_text

log = structlog.get_logger()

SUNDAY = 6


class WeeklyLLM(Protocol):
    async def generate[T: BaseModel](self, prompt: str, system: str, schema: type[T]) -> T: ...


def week_start(day: date) -> date:
    """Monday of the ISO week containing `day`."""
    return day - timedelta(days=day.weekday())


def parse_iso_week(value: str) -> date:
    """'2026-W44' → Monday of that ISO week."""
    return date.fromisoformat(f"{value}-1")


def due_week(today: date, existing: set[date]) -> date | None:
    """Week (its Monday) whose recap should be generated today, or None.

    Sunday: the current week. Monday-Saturday: last week, only if its recap is missing.
    """
    target = week_start(today) if today.weekday() == SUNDAY else week_start(today) - timedelta(7)
    return None if target in existing else target


async def existing_week_starts(weeks: list[date]) -> set[date]:
    """Weeks whose recap was fully delivered (published and emailed).

    A stored but undelivered recap stays 'due', so the next run re-delivers it
    (generate_weekly returns the stored row without calling Gemini).
    """
    async with get_session_factory()() as session:
        rows = await session.execute(
            select(WeeklyDigest.week_start).where(
                WeeklyDigest.week_start.in_(weeks),
                WeeklyDigest.published_at.isnot(None),
                WeeklyDigest.sent_at.isnot(None),
            )
        )
        return {row[0] for row in rows}


def _prompt(days: list[BulletinData]) -> tuple[str, list[SourceRef]]:
    """Numbered stories across the week; returns the prompt and index → lead source."""
    blocks: list[str] = []
    refs: list[SourceRef] = []
    for data in days:
        lines = [
            f"## {italian_date(data.issue_date)} ({data.issue_date.isoformat()}): {data.title}"
        ]
        if data.key_points:
            lines.append(f"Punti del giorno: {' '.join(data.key_points)}")
        elif data.editorial:
            lines.append(f"Editoriale: {data.editorial}")
        for story in data.top_stories:
            outlets = ", ".join(s.name for s in story.sources)
            lines.append(f"[{len(refs)}] {story.title} ({outlets}): {story.text}")
            # The lead link stands for the whole story: paid if any source is, so the
            # public weekly hides the arc text just as the daily page hid the story text.
            paid = any(s.paid for s in story.sources)
            refs.append(story.sources[0].model_copy(update={"paid": paid}))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), refs


async def generate_weekly(
    start: date,
    *,
    force: bool = False,
    gemini: WeeklyLLM | None = None,
    settings: Settings | None = None,
) -> WeeklyDigest | None:
    """Build and store the recap for the week starting `start`. None if nothing to recap or LLM fails."""
    end = start + timedelta(days=6)
    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(WeeklyDigest).where(WeeklyDigest.week_start == start))
        ).scalar_one_or_none()
        if existing is not None and not force:
            log.info("weekly_exists", week_start=str(start))
            return existing
        bulletins = (
            (
                await session.execute(
                    select(Bulletin)
                    .where(
                        Bulletin.issue_date >= start,
                        Bulletin.issue_date <= end,
                        Bulletin.edition == DAILY_EDITION,  # thematic editions are not recapped
                    )
                    .order_by(Bulletin.issue_date)
                )
            )
            .scalars()
            .all()
        )
        days = [BulletinData.model_validate_json(b.content_json) for b in bulletins]

    if not days:
        log.warning("weekly_no_bulletins", week_start=str(start))
        return None

    if gemini is None:
        from feed_brain.ai.gemini import Gemini

        gemini = Gemini(settings or get_settings())

    prompt, refs = _prompt(days)
    try:
        draft = await gemini.generate(prompt, WEEKLY_PROMPT, WeeklyDraft)
    except GeminiError as e:
        log.error("weekly_writer_failed", week_start=str(start), error=str(e))
        return None

    data = WeeklyData(
        week_start=start,
        week_end=end,
        title=clean_text(draft.title).strip() or f"La settimana del {italian_date(start)}",
        subtitle=clean_text(draft.subtitle),
        intro=clean_text(draft.intro),
        arcs=[
            WeeklyArcData(
                title=clean_text(arc.title),
                text=clean_text(arc.text),
                links=[refs[i] for i in dict.fromkeys(arc.story_indices) if 0 <= i < len(refs)],
            )
            for arc in draft.arcs
        ],
        reflection=clean_text(draft.reflection),
        bulletins=[BulletinRef(issue_date=d.issue_date, title=d.title) for d in days],
        stories_count=len(refs),
    )

    async with factory() as session:
        if existing is not None:
            await session.execute(delete(WeeklyDigest).where(WeeklyDigest.id == existing.id))
        digest = WeeklyDigest(
            week_start=start,
            title=data.title,
            subtitle=data.subtitle,
            content_json=data.model_dump_json(),
        )
        session.add(digest)
        try:
            await session.commit()
        except IntegrityError:
            # A concurrent run stored this week first: keep theirs.
            await session.rollback()
            log.warning("weekly_concurrent_insert", week_start=str(start))
            return (
                await session.execute(select(WeeklyDigest).where(WeeklyDigest.week_start == start))
            ).scalar_one()

    log.info("weekly_generated", week_start=str(start), bulletins=len(days), arcs=len(data.arcs))
    return digest
