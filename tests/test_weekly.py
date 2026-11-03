# ABOUTME: Tests for the Sunday weekly recap: due logic, generation from stored bulletins, idempotency.
# ABOUTME: Gemini is faked; bulletins are seeded as BulletinData JSON in in-memory SQLite.

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from feed_brain.ai.gemini import GeminiError
from feed_brain.db.models import Bulletin, WeeklyDigest
from feed_brain.models import (
    BulletinData,
    Section,
    SourceRef,
    TopStory,
    WeeklyArc,
    WeeklyData,
    WeeklyDraft,
)
from feed_brain.services.weekly import due_week, generate_weekly, parse_iso_week, week_start

MONDAY = date(2026, 10, 26)  # ISO week 2026-W44
SUNDAY = date(2026, 11, 1)


def test_week_start_and_iso_week_parsing():
    assert week_start(SUNDAY) == MONDAY
    assert week_start(MONDAY) == MONDAY
    assert parse_iso_week("2026-W44") == MONDAY


def test_due_week_rules():
    # Sunday: this week's recap, unless already there
    assert due_week(SUNDAY, set()) == MONDAY
    assert due_week(SUNDAY, {MONDAY}) is None
    # Monday-Saturday: catch up last week only if missing
    assert due_week(date(2026, 11, 2), set()) == MONDAY
    assert due_week(date(2026, 11, 3), {MONDAY}) is None
    assert due_week(date(2026, 11, 7), set()) == MONDAY


def _bulletin_data(day: date, n: int) -> BulletinData:
    return BulletinData(
        issue_date=day,
        title=f"Bollettino {day}",
        subtitle="",
        editorial=f"Editoriale del {day}.",
        top_stories=[
            TopStory(
                title=f"Storia {day} {i}",
                text="Testo.",
                section=Section.SECURITY,
                sources=[SourceRef(name="Krebs", title=f"T{i}", url=f"https://k/{day}/{i}")],
            )
            for i in range(n)
        ],
        sections=[],
        articles_count=n,
    )


async def _seed(session_factory, days):
    async with session_factory() as session:
        for day in days:
            data = _bulletin_data(day, 2)
            session.add(
                Bulletin(
                    issue_date=day,
                    title=data.title,
                    subtitle="",
                    editorial=data.editorial,
                    content_json=data.model_dump_json(),
                    articles_count=2,
                )
            )
        await session.commit()


class FakeGemini:
    def __init__(self, fail=False):
        self.prompts = []
        self.fail = fail

    async def generate(self, prompt, system, schema):  # noqa: ARG002 - protocol signature
        assert schema is WeeklyDraft
        self.prompts.append(prompt)
        if self.fail:
            raise GeminiError("down")
        return WeeklyDraft(
            title="La settimana",
            subtitle="Sotto",
            intro="Intro.",
            arcs=[WeeklyArc(title="Arco", text="Racconto.", story_indices=[0, 3, 99])],
            reflection="Riflessione.",
        )


async def test_generate_weekly_from_week_bulletins(session_factory):
    await _seed(
        session_factory,
        [MONDAY + timedelta(days=d) for d in range(7)] + [MONDAY - timedelta(days=1)],
    )
    gemini = FakeGemini()

    digest = await generate_weekly(MONDAY, gemini=gemini)

    data = WeeklyData.model_validate_json(digest.content_json)
    assert (data.week_start, data.week_end) == (MONDAY, SUNDAY)
    assert data.title == "La settimana"
    assert len(data.bulletins) == 7  # the previous Sunday is outside the week
    assert data.stories_count == 14
    assert [link.url for link in data.arcs[0].links] == [
        f"https://k/{MONDAY}/0",
        f"https://k/{MONDAY + timedelta(days=1)}/1",
    ]  # index 99 dropped
    prompt = gemini.prompts[0]
    assert "[0] Storia 2026-10-26 0" in prompt and "Editoriale del 2026-11-01." in prompt
    assert "2026-10-25" not in prompt


async def test_generate_weekly_is_idempotent_and_force_regenerates(session_factory):
    await _seed(session_factory, [MONDAY])
    first = await generate_weekly(MONDAY, gemini=FakeGemini())
    gemini = FakeGemini()
    assert (await generate_weekly(MONDAY, gemini=gemini)).id == first.id
    assert gemini.prompts == []

    await generate_weekly(MONDAY, force=True, gemini=gemini)
    assert len(gemini.prompts) == 1
    async with session_factory() as session:
        assert len((await session.execute(select(WeeklyDigest))).scalars().all()) == 1


@pytest.mark.usefixtures("session_factory")
async def test_generate_weekly_without_bulletins_returns_none():
    assert await generate_weekly(MONDAY, gemini=FakeGemini()) is None


async def test_generate_weekly_gemini_failure_saves_nothing(session_factory):
    await _seed(session_factory, [MONDAY])
    assert await generate_weekly(MONDAY, gemini=FakeGemini(fail=True)) is None
    async with session_factory() as session:
        assert (await session.execute(select(WeeklyDigest))).scalars().all() == []


async def test_existing_week_starts_counts_only_delivered_recaps(session_factory):
    from datetime import UTC, datetime

    from feed_brain.services.weekly import existing_week_starts

    previous = MONDAY - timedelta(days=7)
    async with session_factory() as session:
        session.add_all(
            [
                WeeklyDigest(week_start=MONDAY, title="t", subtitle="", content_json="{}"),
                WeeklyDigest(
                    week_start=previous,
                    title="t",
                    subtitle="",
                    content_json="{}",
                    published_at=datetime.now(UTC),
                    sent_at=datetime.now(UTC),
                ),
            ]
        )
        await session.commit()
    assert await existing_week_starts([MONDAY, previous]) == {previous}


async def test_weekly_title_falls_back_when_blank(session_factory):
    await _seed(session_factory, [MONDAY])

    class Blank(FakeGemini):
        async def generate(self, prompt, system, schema):
            draft = await super().generate(prompt, system, schema)
            return draft.model_copy(update={"title": " \x00 "})

    digest = await generate_weekly(MONDAY, gemini=Blank())
    assert digest.title == "La settimana del 26 ottobre 2026"


def test_a_story_with_any_paid_source_gives_a_paid_weekly_link():
    """The daily page hides such a story's text, so the weekly arc built on it must too."""
    from feed_brain.services.weekly import _prompt

    free = SourceRef(name="RSS", title="A", url="https://a/1")
    paid = SourceRef(name="Sub", title="B", url="https://b.substack.com/p/1", paid=True)
    day = BulletinData(
        issue_date=date(2026, 11, 2),
        title="T",
        subtitle="",
        editorial="",
        top_stories=[
            TopStory(
                title="S", text="Sintesi riservata", section=Section.POLITICS, sources=[free, paid]
            )
        ],
        sections=[],
        articles_count=2,
    )
    _, refs = _prompt([day])
    assert (refs[0].url, refs[0].paid) == ("https://a/1", True)


def test_weekly_prompt_uses_key_points_and_falls_back_to_the_prose_editorial():
    from feed_brain.services.weekly import _prompt

    new = _bulletin_data(MONDAY, 1).model_copy(
        update={"editorial": "", "key_points": ["Primo fatto.", "Secondo fatto."]}
    )
    old = _bulletin_data(MONDAY + timedelta(days=1), 1)

    prompt, _ = _prompt([new, old])

    assert "Punti del giorno: Primo fatto. Secondo fatto." in prompt
    assert f"Editoriale: Editoriale del {MONDAY + timedelta(days=1)}." in prompt
