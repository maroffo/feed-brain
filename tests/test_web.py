# ABOUTME: Tests for the public vote service (`feed-brain serve`): signed links, page, vote storage.
# ABOUTME: In-process ASGI client on the test loop; the DB is in-memory SQLite.

import json
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from feed_brain.config import Settings
from feed_brain.db.models import Bulletin, StoryVote
from feed_brain.models import BriefItem, BulletinData, Section, SectionBlock, SourceRef, TopStory
from feed_brain.services.feedback import VoteLinks, signature, story_key
from feed_brain.web import create_app

SECRET = "k" * 32
SETTINGS = Settings(_env_file=None, feedback_key=SecretStr(SECRET), feedback_max_age_days=30)
TODAY = datetime.now(UTC).date()
LEAD = "https://g/fridges"


def _issue(day) -> BulletinData:
    return BulletinData(
        issue_date=day,
        title="t",
        top_stories=[
            TopStory(
                title="Frigoriferi <b>sotto</b> attacco",
                text="x",
                section=Section.SECURITY,
                sources=[SourceRef(name="Guardian", title="Fridges", url=LEAD)],
            )
        ],
        sections=[
            SectionBlock(
                key=Section.POLITICS,
                title="Politica",
                items=[
                    BriefItem(
                        headline="Midterm USA",
                        summary="s",
                        sources=[SourceRef(name="Il Post", title="M", url="https://ilpost/m")],
                    )
                ],
            )
        ],
        articles_count=2,
    )


async def _seed(factory, day=TODAY) -> None:
    async with factory() as session:
        session.add(
            Bulletin(
                issue_date=day,
                title="t",
                subtitle="",
                editorial="",
                content_json=_issue(day).model_dump_json(),
                articles_count=2,
            )
        )
        await session.commit()


def _path(link: str) -> str:
    return link.removeprefix("https://fb.run.app")


def _link(vote: str, url: str = LEAD, day=TODAY) -> str:
    return _path(VoteLinks("https://fb.run.app", SECRET.encode(), day)(url)[vote])


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(SETTINGS))
    return httpx.AsyncClient(transport=transport, base_url="https://fb.run.app")


async def _votes(factory) -> list[StoryVote]:
    async with factory() as session:
        return list((await session.execute(select(StoryVote))).scalars())


async def test_opening_a_link_shows_the_story_and_records_nothing(session_factory):
    await _seed(session_factory)
    async with _client() as client:
        page = await client.get(_link("up"))
    assert page.status_code == 200
    assert "Frigoriferi &lt;b&gt;sotto&lt;/b&gt; attacco" in page.text
    assert 'method: "POST"' in page.text  # the page's script casts the vote
    assert '<noscript><form method="post">' in page.text  # fallback without JS
    assert "Registrato: più così" in page.text and "👍" not in page.text  # same words as the email
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert await _votes(session_factory) == []


async def test_post_records_the_vote_with_a_snapshot_and_the_last_vote_wins(session_factory):
    await _seed(session_factory)
    async with _client() as client:
        first = await client.post(_link("up"))
        second = await client.post(_link("down"))
    assert first.status_code == 200 and second.status_code == 200
    assert "Registrato: meno così" in second.text
    (vote,) = await _votes(session_factory)
    assert vote.vote == "down" and vote.issue_date == TODAY
    assert vote.story_key == story_key(LEAD) and vote.section == "security"
    assert vote.headline == "Frigoriferi <b>sotto</b> attacco"
    assert json.loads(vote.sources_json)[0]["url"] == LEAD


async def test_briefs_can_be_voted_too(session_factory):
    await _seed(session_factory)
    async with _client() as client:
        await client.post(_link("down", "https://ilpost/m"))
    (vote,) = await _votes(session_factory)
    assert vote.headline == "Midterm USA" and vote.section == "politics" and vote.vote == "down"


async def test_undo_removes_the_vote(session_factory):
    await _seed(session_factory)
    async with _client() as client:
        await client.post(_link("up"))
        undone = await client.post(_link("up") + "&undo=1")
    assert undone.status_code == 200 and "annullato" in undone.text
    assert await _votes(session_factory) == []


async def test_forged_or_malformed_links_are_refused_without_writing(session_factory):
    await _seed(session_factory)
    key = story_key(LEAD)
    good = signature(SECRET.encode(), TODAY, key, "up")
    async with _client() as client:
        tampered = await client.post(f"/v/{TODAY}/{key}/down?s={good}")
        unsigned = await client.post(f"/v/{TODAY}/{key}/up")
        bad_vote = await client.post(f"/v/{TODAY}/{key}/meh?s={good}")
        bad_date = await client.post(f"/v/2026-13-40/{key}/up?s={good}")
        non_ascii = await client.post(f"/v/{TODAY}/{key}/up?s=%C3%A8")
    assert tampered.status_code == 403 and unsigned.status_code == 403
    assert non_ascii.status_code == 403
    assert bad_vote.status_code == 404 and bad_date.status_code == 404
    assert await _votes(session_factory) == []


async def test_old_issues_and_vanished_stories_are_gone(session_factory):
    old = TODAY - timedelta(days=31)
    await _seed(session_factory, old)
    await _seed(session_factory)
    async with _client() as client:
        expired = await client.post(_link("up", day=old))
        vanished = await client.post(_link("up", "https://not/in/issue"))
        no_issue = await client.post(_link("up", day=TODAY - timedelta(days=2)))
    assert expired.status_code == 410 and "scaduto" in expired.text
    assert vanished.status_code == 410 and "non più presente" in vanished.text
    assert no_issue.status_code == 410
    assert await _votes(session_factory) == []


async def test_health_check(session_factory):  # noqa: ARG001
    # not /healthz: Cloud Run reserves paths ending in "z" and answers them itself with a 404
    async with _client() as client:
        response = await client.get("/health")
    assert response.status_code == 200 and response.text == "ok"


def test_the_service_refuses_to_start_without_a_real_key():
    import pytest

    for key in (None, SecretStr(""), SecretStr("   ")):
        with pytest.raises(ValueError, match="FEEDBACK_KEY"):
            create_app(Settings(_env_file=None, feedback_key=key))


async def test_the_page_script_handles_network_errors_and_undoes_on_the_query_string(
    session_factory,
):
    await _seed(session_factory)
    async with _client() as client:
        page = await client.get(_link("up"))
    assert page.text.count(".catch(") == 2
    assert "location.pathname + location.search" in page.text
    assert "location.href" not in page.text  # a #fragment would swallow '&undo=1'
