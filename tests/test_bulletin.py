# ABOUTME: Tests for daily bulletin generation: candidates, idempotency, force, analysis scope, assembly.
# ABOUTME: Gemini (clustering + writer) and Opus (deep_analyze) are faked; DB is in-memory SQLite.

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update

from feed_brain.db.models import Article, Bulletin, FeedSource
from feed_brain.models import (
    BulletinData,
    BulletinDraft,
    DeepAnalysis,
    Section,
    WrittenStory,
)
from feed_brain.services import bulletin as bulletin_mod
from feed_brain.services.bulletin import assemble, first_sentence, generate_bulletin, italian_date
from feed_brain.services.clustering import ClusterOut, ClusterResponse
from feed_brain.services.ranking import Candidate, Story

ISSUE = date(2026, 11, 2)
ANALYSIS = DeepAnalysis(summary="Analisi.", insights=["i1"], money_quote="Q", actionables=[])


def _cand(article_id, section="security", tier="high", source="Krebs", confidence=0.9):
    return Candidate(
        article_id=article_id,
        title=f"Title {article_id}",
        url=f"https://x/{article_id}",
        source=source,
        tier=tier,
        section=section,
        confidence=confidence,
        summary=f"Prima frase {article_id}. Seconda frase.",
        content_len=100,
    )


def test_first_sentence_and_italian_date():
    assert first_sentence("Uno. Due. Tre.") == "Uno."
    assert first_sentence("Senza punto") == "Senza punto."
    assert first_sentence("") == ""
    assert italian_date(date(2026, 11, 2)) == "2 novembre 2026"


def test_assemble_uses_writer_text_with_fallback_and_orders_sections():
    top = [
        Story("security", [_cand(1), _cand(2, source="Schneier")], "Breccia"),
        Story("ai", [_cand(3, section="ai")], "Agenti"),
    ]
    rest = [
        Story("sport", [_cand(4, section="sport", tier="medium")], "Rugby"),
        Story("tech", [_cand(5, section="tech", tier="medium")], "Chip"),
    ]
    radar = [_cand(6, section="threat_radar", tier="low")]
    draft = BulletinDraft(
        title="T",
        subtitle="S",
        editorial="E",
        top_stories=[WrittenStory(index=0, title="Breccia grave", text="Testo.")],
    )

    data = assemble(ISSUE, draft, top, rest, radar, {1: ANALYSIS}, articles_count=6)

    assert (data.title, data.subtitle, data.editorial) == ("T", "S", "E")
    assert data.top_stories[0].title == "Breccia grave"
    assert [s.name for s in data.top_stories[0].sources] == ["Krebs", "Schneier"]
    assert data.top_stories[0].analysis == ANALYSIS
    # index 1 missing from draft: headline + lead summary
    assert data.top_stories[1].title == "Agenti"
    assert data.top_stories[1].text == "Prima frase 3. Seconda frase."
    assert data.top_stories[1].analysis is None
    assert [b.key for b in data.sections] == [Section.TECH, Section.SPORT]
    assert data.sections[0].items[0].summary == "Prima frase 5."
    assert data.radar[0].title == "Title 6"


def test_assemble_without_draft_degrades_gracefully():
    data = assemble(ISSUE, None, [Story("ai", [_cand(1, section="ai")], "H")], [], [], {}, 1)
    assert data.title == "Il bollettino del 2 novembre 2026"
    assert data.top_stories[0].title == "H"


class FakeGemini:
    def __init__(self, fail_writer=False, written=None, on_write=None, cluster_error=None):
        self.calls: list[str] = []
        self.fail_writer = fail_writer
        self.written = written or []
        self.on_write = on_write
        self.cluster_error = cluster_error
        self.writer_prompt = ""

    async def generate_text(self, prompt, system):  # noqa: ARG002 - protocol signature
        return "answer"

    async def generate(self, prompt, system, schema):  # noqa: ARG002 - protocol signature
        if schema is ClusterResponse:
            self.calls.append("cluster")
            if self.cluster_error:
                raise self.cluster_error
            ids = [
                int(line.split("]")[0][1:]) for line in prompt.splitlines() if line.startswith("[")
            ]
            return ClusterResponse(stories=[ClusterOut(article_ids=ids, headline="Storia")])
        assert schema is BulletinDraft
        self.calls.append("write")
        self.writer_prompt = prompt
        if self.on_write:
            await self.on_write()
        if self.fail_writer:
            from feed_brain.ai.gemini import GeminiError

            raise GeminiError("down")
        return BulletinDraft(
            title="Titolo", subtitle="Sotto", editorial="Il punto.", top_stories=self.written
        )


@pytest.fixture
def analyzed(monkeypatch):
    calls: list[int] = []

    async def fake_deep_analyze(article, client, gemini, settings=None):  # noqa: ARG001
        calls.append(article.id)
        return ANALYSIS

    monkeypatch.setattr(bulletin_mod, "deep_analyze", fake_deep_analyze)
    return calls


async def _seed(session_factory) -> dict[str, int]:
    now = datetime.now(UTC)
    async with session_factory() as session:
        krebs = FeedSource(name="Krebs", url="https://krebs/feed", folder="0. Security")
        post = FeedSource(name="Il Post", url="https://post/feed", folder="News")
        session.add_all([krebs, post])
        await session.flush()

        def art(title, source, **kw):
            defaults = {
                "url": f"https://x/{title}",
                "title": title,
                "source_id": source.id,
                "content": "body",
                "status": "triaged",
                "tier": "high",
                "section": "security",
                "confidence": 0.9,
                "summary": f"{title} sintesi. Altro.",
                "fetched_at": now,
            }
            defaults.update(kw)
            return Article(**defaults)

        articles = [
            art("breach-a", krebs),
            art("breach-b", post),
            art("politics", post, tier="medium", section="politics", confidence=0.6),
            art("low-tech", post, tier="low", section="tech"),
            art("radar-low", krebs, tier="low", section="threat_radar"),
            art("old", krebs, fetched_at=now - timedelta(hours=72)),
            art("untriaged", krebs, status="new", tier=None, section=None),
        ]
        session.add_all(articles)
        await session.commit()
        return {a.title: a.id for a in articles}


async def test_generate_bulletin_persists_stamps_and_is_idempotent(session_factory, analyzed):
    ids = await _seed(session_factory)
    gemini = FakeGemini()

    bulletin = await generate_bulletin(ISSUE, gemini=gemini, anthropic=object())

    assert bulletin is not None
    data = BulletinData.model_validate_json(bulletin.content_json)
    assert data.title == "Titolo"
    assert data.articles_count == 4
    assert [r.title for r in data.radar] == ["radar-low"]
    assert {s.title for s in data.top_stories} == {"Storia"}
    async with session_factory() as session:
        stamped = {
            a.title: a.bulletin_id for a in (await session.execute(select(Article))).scalars()
        }
    assert {t for t, b in stamped.items() if b == bulletin.id} == {
        "breach-a",
        "breach-b",
        "politics",
        "radar-low",
    }
    assert analyzed  # leads of top stories analyzed
    async with session_factory() as session:
        lead = await session.get(Article, analyzed[0])
        assert lead.deep_summary == "Analisi."
        assert lead.status == "analyzed"

    calls_before = list(gemini.calls)
    again = await generate_bulletin(ISSUE, gemini=gemini, anthropic=object())
    assert again.id == bulletin.id
    assert gemini.calls == calls_before  # no-op: no LLM calls
    assert ids  # seeded


async def test_force_regenerates_and_restamps(session_factory, analyzed):
    await _seed(session_factory)
    first = await generate_bulletin(ISSUE, gemini=FakeGemini(), anthropic=object())
    async with session_factory() as session:
        row = await session.get(Bulletin, first.id)
        row.sent_at = datetime.now(UTC)  # sentinel: a regenerated row starts unsent
        await session.commit()
    analyzed.clear()

    second = await generate_bulletin(ISSUE, force=True, gemini=FakeGemini(), anthropic=object())

    assert second.sent_at is None
    assert analyzed == []  # analyses stored on the first run are reused
    async with session_factory() as session:
        bulletins = (await session.execute(select(Bulletin))).scalars().all()
        stamped = {a.bulletin_id for a in (await session.execute(select(Article))).scalars()}
    assert [b.id for b in bulletins] == [second.id]
    assert stamped == {None, second.id}
    assert BulletinData.model_validate_json(second.content_json).articles_count == 4


async def test_no_anthropic_client_skips_analysis(session_factory, analyzed):
    await _seed(session_factory)
    bulletin = await generate_bulletin(ISSUE, gemini=FakeGemini(), anthropic=None)
    assert analyzed == []
    data = BulletinData.model_validate_json(bulletin.content_json)
    assert all(s.analysis is None for s in data.top_stories)


@pytest.mark.usefixtures("analyzed")
async def test_writer_failure_still_produces_bulletin(session_factory):
    await _seed(session_factory)
    bulletin = await generate_bulletin(ISSUE, gemini=FakeGemini(fail_writer=True), anthropic=None)
    assert bulletin.title == "Il bollettino del 2 novembre 2026"


@pytest.mark.usefixtures("session_factory")
async def test_no_candidates_returns_none():
    assert await generate_bulletin(ISSUE, gemini=FakeGemini(), anthropic=None) is None


def test_short_source_name():
    from feed_brain.services.bulletin import short_source_name

    assert short_source_name("Over Security - Cybersecurity news aggregator") == "Over Security"
    assert short_source_name("DDay.it, news, articoli, guide, gallery e video") == "DDay.it"
    assert short_source_name("Krebs on Security") == "Krebs on Security"
    assert short_source_name("SANS ISC | InfoCON: green") == "SANS ISC"
    assert short_source_name("Notiziole di .mau.") == "Notiziole di .mau."
    assert short_source_name(None) == "Fonte sconosciuta"


def test_assemble_caps_briefs_per_section_and_counts_the_rest():
    rest = [Story("tech", [_cand(i, section="tech", tier="medium")], f"H{i}") for i in range(1, 6)]
    data = assemble(ISSUE, None, [], rest, [], {}, 5, section_cap=3)
    assert [i.headline for i in data.sections[0].items] == ["H1", "H2", "H3"]
    assert data.sections[0].omitted == 2


def test_assemble_whitespace_titles_fall_back():
    draft = BulletinDraft(
        title="  \x00 ",
        subtitle="",
        editorial="",
        top_stories=[WrittenStory(index=0, title="\x01", text=" ")],
    )
    data = assemble(ISSUE, draft, [Story("ai", [_cand(1, section="ai")], "H")], [], [], {}, 1)
    assert data.title == "Il bollettino del 2 novembre 2026"
    assert data.top_stories[0].title == "H"
    assert data.top_stories[0].text == "Prima frase 1. Seconda frase."


@pytest.mark.usefixtures("analyzed")
async def test_writer_stories_and_analysis_reach_the_issue(session_factory):
    await _seed(session_factory)
    gemini = FakeGemini(written=[WrittenStory(index=0, title="Scritto", text="Dal writer.")])
    bulletin = await generate_bulletin(ISSUE, gemini=gemini, anthropic=object())
    data = BulletinData.model_validate_json(bulletin.content_json)
    assert data.top_stories[0].title == "Scritto"
    assert data.top_stories[0].lead_url.startswith("https://x/")
    assert "Analisi: Analisi." in gemini.writer_prompt and "* i1" in gemini.writer_prompt


@pytest.mark.usefixtures("analyzed")
async def test_radar_overflow_is_stamped(session_factory):
    from feed_brain.config import Settings

    await _seed(session_factory)
    async with session_factory() as session:
        session.add(
            Article(
                url="https://x/radar-2",
                title="radar-2",
                content="b",
                status="triaged",
                tier="low",
                section="threat_radar",
                confidence=0.1,
                summary="s",
                fetched_at=datetime.now(UTC),
            )
        )
        await session.commit()
    settings = Settings(_env_file=None, radar_cap=1)
    bulletin = await generate_bulletin(
        ISSUE, gemini=FakeGemini(), anthropic=None, settings=settings
    )
    assert len(BulletinData.model_validate_json(bulletin.content_json).radar) == 1
    async with session_factory() as session:
        radar = (
            (await session.execute(select(Article).where(Article.section == "threat_radar")))
            .scalars()
            .all()
        )
    assert {a.bulletin_id for a in radar} == {bulletin.id}


@pytest.mark.usefixtures("analyzed")
async def test_force_reuses_aged_out_articles_of_the_old_issue(session_factory):
    await _seed(session_factory)
    await generate_bulletin(ISSUE, gemini=FakeGemini(), anthropic=None)
    async with session_factory() as session:
        await session.execute(
            update(Article).values(fetched_at=datetime.now(UTC) - timedelta(days=5))
        )
        await session.commit()

    second = await generate_bulletin(ISSUE, force=True, gemini=FakeGemini(), anthropic=None)

    assert second is not None
    assert BulletinData.model_validate_json(second.content_json).articles_count == 4


@pytest.mark.usefixtures("analyzed")
async def test_force_without_any_candidate_keeps_the_issue(session_factory):
    await _seed(session_factory)
    first = await generate_bulletin(ISSUE, gemini=FakeGemini(), anthropic=None)
    async with session_factory() as session:
        await session.execute(update(Article).values(status="new"))
        await session.commit()
    again = await generate_bulletin(ISSUE, force=True, gemini=FakeGemini(), anthropic=None)
    assert again.id == first.id


@pytest.mark.usefixtures("analyzed")
async def test_failed_force_keeps_the_previous_issue(session_factory):
    await _seed(session_factory)
    first = await generate_bulletin(ISSUE, gemini=FakeGemini(), anthropic=None)

    with pytest.raises(RuntimeError):
        await generate_bulletin(
            ISSUE, force=True, gemini=FakeGemini(cluster_error=RuntimeError("boom")), anthropic=None
        )

    async with session_factory() as session:
        rows = (await session.execute(select(Bulletin))).scalars().all()
        stamped = {a.bulletin_id for a in (await session.execute(select(Article))).scalars()}
    assert [r.id for r in rows] == [first.id]
    assert first.id in stamped


@pytest.mark.usefixtures("analyzed")
async def test_concurrent_insert_returns_the_winner(session_factory):
    await _seed(session_factory)

    async def insert_competitor():
        async with session_factory() as session:
            session.add(
                Bulletin(
                    issue_date=ISSUE,
                    title="Altro run",
                    subtitle="",
                    editorial="",
                    content_json="{}",
                    articles_count=0,
                )
            )
            await session.commit()

    bulletin = await generate_bulletin(
        ISSUE, gemini=FakeGemini(on_write=insert_competitor), anthropic=None
    )
    assert bulletin.title == "Altro run"


async def test_articles_from_the_newsletter_inbox_are_marked_paid(session_factory):
    """Candidates from email sources carry paid=True into the stored issue's source refs."""
    from feed_brain.services.bulletin import _load_candidates, _sources
    from feed_brain.services.ranking import Story

    now = datetime.now(UTC)
    async with session_factory() as session:
        inbox = FeedSource(name="Carretta", url="email:carretta@substack.com", feed_type="email")
        rss = FeedSource(name="Il Post", url="https://post/feed")
        session.add_all([inbox, rss])
        await session.flush()
        for title, source in (("paid-post", inbox), ("free-post", rss)):
            session.add(
                Article(
                    url=f"https://x/{title}",
                    title=title,
                    source_id=source.id,
                    content="body",
                    status="triaged",
                    tier="high",
                    section="politics",
                    confidence=0.9,
                    summary="s.",
                    fetched_at=now,
                )
            )
        await session.commit()
        candidates = await _load_candidates(session, window_hours=36)

    paid = {c.title: c.paid for c in candidates}
    assert paid == {"paid-post": True, "free-post": False}
    refs = _sources(Story(section="politics", articles=candidates, headline="h"))
    assert {r.title: r.paid for r in refs} == paid


async def test_forwarded_newsletters_are_candidates_whatever_their_tier(session_factory):
    """Max picked every forwarded newsletter by hand, so a low triage tier must not drop it."""
    from feed_brain.services.bulletin import _load_candidates

    now = datetime.now(UTC)
    async with session_factory() as session:
        inbox = FeedSource(name="Polpette", url="email:vanz@substack.com", feed_type="email")
        rss = FeedSource(name="Il Post", url="https://post/feed")
        session.add_all([inbox, rss])
        await session.flush()
        for title, source in (("low-newsletter", inbox), ("low-rss", rss)):
            session.add(
                Article(
                    url=f"https://x/{title}",
                    title=title,
                    source_id=source.id,
                    content="body",
                    status="triaged",
                    tier="low",
                    section="culture",
                    confidence=0.5,
                    summary="s.",
                    fetched_at=now,
                )
            )
        await session.commit()
        candidates = await _load_candidates(session, window_hours=36)

    assert [c.title for c in candidates] == ["low-newsletter"]
