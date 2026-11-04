# ABOUTME: `feed-brain serve`: the public service behind the signed links of the daily email (votes, essays).
# ABOUTME: Vote GET shows the story and its script casts the vote (scanners run no JS); essays are read-only.

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response
from starlette.routing import Route

from feed_brain.config import Settings, get_settings
from feed_brain.db.models import Bulletin, StoryVote
from feed_brain.db.session import close_db, get_session_factory
from feed_brain.models import BulletinData
from feed_brain.services.feedback import (
    ESSAY,
    VOTES,
    StorySnapshot,
    find_story,
    find_top_story,
    link_secret,
    valid_signature,
)
from feed_brain.services.render import render_essay_page, render_vote_page

log = structlog.get_logger()

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
LABELS = {"up": "più così", "down": "meno così"}  # the words of the email


def _page(status: int, message: str, **page: str) -> HTMLResponse:
    html = render_vote_page(message=message, state=page.pop("state", "error"), **page)
    return HTMLResponse(html, status_code=status, headers=HEADERS)


async def _record(day: date, key: str, vote: str, story: StorySnapshot) -> None:
    """Insert or update the vote on this story (last click wins)."""
    sources = json.dumps([s.model_dump() for s in story.sources], ensure_ascii=False)
    match = (StoryVote.issue_date == day, StoryVote.story_key == key)
    for attempt in range(2):
        async with get_session_factory()() as session:
            row = (await session.execute(select(StoryVote).where(*match))).scalar_one_or_none()
            now = datetime.now(UTC)
            if row is None:
                row = StoryVote(issue_date=day, story_key=key, created_at=now)
                session.add(row)
            row.vote = vote
            row.headline = story.headline[:500]
            row.section = story.section
            row.sources_json = sources
            row.updated_at = now
            try:
                await session.commit()
                return
            except IntegrityError:
                # Two clicks raced on a story with no vote yet: the other insert won, update it.
                await session.rollback()
                if attempt:
                    raise


async def _issue(day: date) -> BulletinData | None:
    async with get_session_factory()() as session:
        content = (
            await session.execute(select(Bulletin.content_json).where(Bulletin.issue_date == day))
        ).scalar_one_or_none()
    return BulletinData.model_validate_json(content) if content else None


async def _undo(day: date, key: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            delete(StoryVote).where(StoryVote.issue_date == day, StoryVote.story_key == key)
        )
        await session.commit()


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or get_settings()
    secret = link_secret(settings)
    if secret is None:
        raise ValueError("FEEDBACK_KEY (at least 32 characters) is required to check vote links")
    max_age = timedelta(days=settings.feedback_max_age_days)

    async def resolve(request: Request) -> tuple[date, str, str, StorySnapshot] | Response:
        """Checks in order: shape, signature (nothing is read before it), age, story."""
        key, vote = request.path_params["key"], request.path_params["vote"]
        try:
            day = date.fromisoformat(request.path_params["day"])
        except ValueError:
            return _page(404, "Link non valido.")
        if vote not in VOTES:
            return _page(404, "Link non valido.")
        if not valid_signature(secret, day, key, vote, request.query_params.get("s", "")):
            return _page(403, "Link non valido.")
        if datetime.now(UTC).date() - day > max_age:
            days = settings.feedback_max_age_days
            return _page(410, f"Link scaduto: si vota sui bollettini degli ultimi {days} giorni.")
        data = await _issue(day)
        story = find_story(data, key) if data else None
        if story is None:
            return _page(410, "Storia non più presente: il bollettino è stato rigenerato.")
        return day, key, vote, story

    async def vote_page(request: Request) -> Response:
        resolved = await resolve(request)
        if isinstance(resolved, Response):
            return resolved
        day, key, vote, story = resolved
        label = LABELS[vote]
        if request.method == "GET":
            return _page(
                200,
                f"Registro: {label}…",
                state="pending",
                headline=story.headline,
                label=label,
            )
        if request.query_params.get("undo") == "1":
            await _undo(day, key)
            log.info("vote_undone", issue_date=str(day), story_key=key)
            return _page(200, "Voto annullato.", state="undone", headline=story.headline)
        await _record(day, key, vote, story)
        log.info("vote_recorded", issue_date=str(day), story_key=key, vote=vote)
        undo_url = f"{request.url.path}?s={request.query_params['s']}&undo=1"
        return _page(
            200,
            f"Registrato: {label}",
            state="recorded",
            headline=story.headline,
            undo_url=undo_url,
        )

    async def essay_page(request: Request) -> Response:
        """Read-only and without expiry; the signature is checked before anything is read."""
        key = request.path_params["key"]

        def error(status: int, message: str) -> HTMLResponse:
            return _page(status, message, kicker="feed-brain · il quadro completo")

        try:
            day = date.fromisoformat(request.path_params["day"])
        except ValueError:
            return error(404, "Link non valido.")
        if not valid_signature(secret, day, key, ESSAY, request.query_params.get("s", "")):
            return error(403, "Link non valido.")
        data = await _issue(day)
        story = find_top_story(data, key) if data else None
        if data is None or story is None:
            return error(410, "Storia non più presente: il bollettino è stato rigenerato.")
        if story.essay is None:
            return error(404, "Nessun approfondimento per questa storia.")
        html = render_essay_page(story, data.issue_date)
        return HTMLResponse(html, headers=HEADERS)

    async def health(_request: Request) -> Response:
        return PlainTextResponse("ok", headers=HEADERS)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        yield
        await close_db()

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/v/{day}/{key}/{vote}", vote_page, methods=["GET", "POST"]),
            Route("/e/{day}/{key}", essay_page, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
