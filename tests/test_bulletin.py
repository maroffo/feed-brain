# ABOUTME: Tests for daily bulletin generation: candidates, idempotency, force, analysis scope, assembly.
# ABOUTME: Gemini (clustering + writer) and Opus (deep_analyze, write_essays) are faked; DB is in-memory SQLite.

import dataclasses
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update

from feed_brain.db.models import Article, Bulletin, FeedSource
from feed_brain.models import (
    BulletinData,
    BulletinDraft,
    DeepAnalysis,
    Section,
    StoryEssay,
    WrittenStory,
)
from feed_brain.services import bulletin as bulletin_mod
from feed_brain.services.bulletin import assemble, first_sentence, generate_bulletin, italian_date
from feed_brain.services.clustering import ClusterOut, ClusterResponse
from feed_brain.services.ranking import Candidate, Story

ISSUE = date(2026, 11, 2)
ANALYSIS = DeepAnalysis(summary="Analisi.", insights=["i1"], money_quote="Q", actionables=[])
ACTIONABLE = DeepAnalysis(
    summary="Analisi.",
    insights=["i1", "i2", "i3"],
    money_quote="Q",
    actionables=["a1", "a2", "a3"],
)
LONG_TEXT = "Testo dell'articolo. " * 100  # above analysis_min_chars


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
        Story("tech", [_cand(5, section="tech", tier="medium")], "Chip"),
        Story("sport", [_cand(4, section="sport", tier="medium")], "Rugby"),
    ]
    draft = BulletinDraft(
        title="T",
        key_points=["Primo fatto.", " ", "\x00", "Secondo fatto."],
        top_stories=[WrittenStory(index=0, title="Breccia grave", text="Testo.")],
    )

    data = assemble(ISSUE, draft, top, rest, {1: ANALYSIS}, articles_count=6)

    assert (data.title, data.subtitle, data.editorial) == ("T", "", "")
    assert data.key_points == ["Primo fatto.", "Secondo fatto."]
    assert data.top_stories[0].title == "Breccia grave"
    assert [s.name for s in data.top_stories[0].sources] == ["Krebs", "Schneier"]
    assert data.top_stories[0].analysis.money_quote == "Q"
    # index 1 missing from draft: headline + lead summary
    assert data.top_stories[1].title == "Agenti"
    assert data.top_stories[1].text == "Prima frase 3. Seconda frase."
    assert data.top_stories[1].analysis is None
    # culture and sport come before the technical sections
    assert [b.key for b in data.sections] == [Section.SPORT, Section.TECH]
    assert data.sections[1].items[0].summary == "Prima frase 5."


def test_assemble_without_draft_degrades_gracefully():
    data = assemble(ISSUE, None, [Story("ai", [_cand(1, section="ai")], "H")], [], {}, 1)
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
        return BulletinDraft(title="Titolo", key_points=["Il punto."], top_stories=self.written)


@pytest.fixture
def analyzed(monkeypatch):
    calls: list[int] = []

    async def fake_deep_analyze(article, client, gemini, settings=None):  # noqa: ARG001
        calls.append(article.id)
        return ANALYSIS

    monkeypatch.setattr(bulletin_mod, "deep_analyze", fake_deep_analyze)
    return calls


@dataclasses.dataclass
class EssayRecorder:
    """What generate_bulletin handed to write_essays, and what the fake returns."""

    top: list = dataclasses.field(default_factory=list)
    articles: dict = dataclasses.field(default_factory=dict)
    make: object = None  # top stories -> {lead article id: StoryEssay}


@pytest.fixture(autouse=True)
def essays(monkeypatch):
    """Opus essays are faked in every test: generate_bulletin always gets a dummy client."""
    recorder = EssayRecorder()

    async def fake_write_essays(top, articles, client, settings, semaphore=None):  # noqa: ARG001
        recorder.top, recorder.articles = list(top), dict(articles)
        return recorder.make(top) if callable(recorder.make) else {}

    monkeypatch.setattr(bulletin_mod, "write_essays", fake_write_essays)
    return recorder


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
                "content": LONG_TEXT,
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
            art("paper", krebs, tier="high", section="research"),
            art("agile", post, tier="high", section="management"),
            art("k8s", post, tier="high", section="cloud_devops"),
            art("gc", krebs, tier="high", section="software"),
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
    assert data.key_points == ["Il punto."]
    assert bulletin.editorial == "Il punto."
    assert data.articles_count == 3
    assert {s.title for s in data.top_stories} == {"Storia"}
    async with session_factory() as session:
        stamped = {
            a.title: a.bulletin_id for a in (await session.execute(select(Article))).scalars()
        }
    assert {t for t, b in stamped.items() if b == bulletin.id} == {
        "breach-a",
        "breach-b",
        "politics",
    }
    # threat radar, research, management, cloud/DevOps and software stay out, unstamped
    assert {stamped[t] for t in ("radar-low", "paper", "agile", "k8s", "gc")} == {None}
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
    assert BulletinData.model_validate_json(second.content_json).articles_count == 3


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
    data = assemble(ISSUE, None, [], rest, {}, 5, section_cap=3)
    assert [i.headline for i in data.sections[0].items] == ["H1", "H2", "H3"]
    assert data.sections[0].omitted == 2


def test_assemble_whitespace_titles_fall_back():
    draft = BulletinDraft(
        title="  \x00 ",
        key_points=[],
        top_stories=[WrittenStory(index=0, title="\x01", text=" ")],
    )
    data = assemble(ISSUE, draft, [Story("ai", [_cand(1, section="ai")], "H")], [], {}, 1)
    assert data.title == "Il bollettino del 2 novembre 2026"
    assert data.top_stories[0].title == "H"
    assert data.top_stories[0].text == "Prima frase 1. Seconda frase."


@pytest.mark.usefixtures("analyzed")
async def test_writer_stories_and_analysis_reach_the_issue(session_factory):
    await _seed(session_factory)
    gemini = FakeGemini(written=[WrittenStory(index=0, title="Scritto", text="Dal writer.")])
    bulletin = await generate_bulletin(ISSUE, gemini=gemini, anthropic=object())
    data = BulletinData.model_validate_json(bulletin.content_json)
    # index 0 is the top-ranked (security) story, shown after the politics one
    written = next(s for s in data.top_stories if s.section == Section.SECURITY)
    assert written.title == "Scritto"
    assert written.lead_url.startswith("https://x/")
    # the writer gets the analysis summary; insights are the issue's own notes
    assert "Analisi: Analisi." in gemini.writer_prompt and "i1" not in gemini.writer_prompt


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
    assert BulletinData.model_validate_json(second.content_json).articles_count == 3


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


def test_stories_built_on_forwarded_newsletters_skip_the_section_cap():
    """Every forwarded newsletter shows up; only RSS stories beyond the cap are folded."""
    rest = [Story("tech", [_cand(i, section="tech", tier="medium")], f"H{i}") for i in range(1, 5)]
    paid = _cand(9, section="tech", tier="low")
    rest.append(Story("tech", [dataclasses.replace(paid, paid=True)], "Newsletter"))

    data = assemble(ISSUE, None, [], rest, {}, 5, section_cap=2)

    assert [i.headline for i in data.sections[0].items] == ["H1", "H2", "Newsletter"]
    assert data.sections[0].omitted == 2
    assert data.sections[0].items[-1].sources[0].paid is True


def test_top_stories_are_grouped_by_area_keeping_importance_inside_each():
    """Chosen by importance, read politics/economy first, then culture/sport, then tech."""
    top = [
        Story("security", [_cand(1)], "Sicurezza"),
        Story("culture", [_cand(2, section="culture")], "Cultura"),
        Story("economy", [_cand(3, section="economy")], "Economia"),
        Story("ai", [_cand(4, section="ai")], "AI"),
        Story("politics", [_cand(5, section="politics")], "Politica"),
    ]
    draft = BulletinDraft(
        title="T", key_points=[], top_stories=[WrittenStory(index=4, title="Voto", text="Testo.")]
    )

    data = assemble(ISSUE, draft, top, [], {}, 5)

    assert [s.title for s in data.top_stories] == ["Economia", "Voto", "Cultura", "Sicurezza", "AI"]


def test_issue_shows_the_quote_always_and_notes_only_for_actionable_stories():
    top = [
        Story("security", [_cand(1)], "Da fare"),
        Story("politics", [_cand(2, section="politics")], "Notizia"),
    ]
    news = DeepAnalysis(summary="S.", insights=["n1", "n2"], money_quote="Q2", actionables=[])

    data = assemble(ISSUE, None, top, [], {1: ACTIONABLE, 2: news}, 2)

    shown = {s.title: s.analysis for s in data.top_stories}
    assert shown["Da fare"].insights == ["i1", "i2"]
    assert shown["Da fare"].actionables == ["a1", "a2"]
    assert shown["Da fare"].money_quote == "Q"
    assert shown["Notizia"].insights == []
    assert shown["Notizia"].money_quote == "Q2"


def test_sections_outside_the_daily_issue_are_left_out():
    rest = [
        Story(section, [_cand(i, section=section, tier="medium")], section)
        for i, section in enumerate(
            ["research", "management", "threat_radar", "cloud_devops", "software", "politics"], 1
        )
    ]
    data = assemble(ISSUE, None, [], rest, {}, 6)
    assert [b.key for b in data.sections] == [Section.POLITICS]


async def test_opus_runs_only_on_the_first_leads_with_enough_text(session_factory, analyzed):
    """Opus covers the first analysis_top_n stories, skipping leads with too little text
    (a title-only feed item would get an analysis of nothing)."""
    from feed_brain.config import Settings

    now = datetime.now(UTC)
    async with session_factory() as session:
        source = FeedSource(name="Il Post", url="https://post/feed")
        session.add(source)
        await session.flush()
        rows = [
            ("first", "security", 0.9, LONG_TEXT),
            ("thin", "politics", 0.8, "Solo il titolo."),
            ("third", "economy", 0.7, LONG_TEXT),
        ]
        articles = [
            Article(
                url=f"https://x/{title}",
                title=title,
                source_id=source.id,
                content=content,
                status="triaged",
                tier="high",
                section=section,
                confidence=confidence,
                summary=f"{title}.",
                fetched_at=now,
            )
            for title, section, confidence, content in rows
        ]
        session.add_all(articles)
        await session.commit()
        ids = {a.title: a.id for a in articles}
    settings = Settings(_env_file=None, analysis_top_n=2, analysis_min_chars=100)

    bulletin = await generate_bulletin(
        ISSUE, gemini=FakeGemini(), anthropic=object(), settings=settings
    )

    assert analyzed == [ids["first"]]
    data = BulletinData.model_validate_json(bulletin.content_json)
    assert [s.analysis is not None for s in data.top_stories] == [False, False, True]


@pytest.mark.usefixtures("analyzed")
async def test_essays_read_every_source_of_the_top_stories_and_land_on_their_story(
    session_factory, essays
):
    ids = await _seed(session_factory)
    essays.make = lambda top: {
        top[0].lead.article_id: StoryEssay(paragraphs=["Uno.", "Due."], sources_read=2)
    }
    bulletin = await generate_bulletin(ISSUE, gemini=FakeGemini(), anthropic=object())
    data = BulletinData.model_validate_json(bulletin.content_json)
    # every article of every top story, not only the leads, with its text loaded
    assert set(essays.articles) == {ids["breach-a"], ids["breach-b"], ids["politics"]}
    assert all(a.content for a in essays.articles.values())
    security = next(s for s in data.top_stories if s.section == Section.SECURITY)
    politics = next(s for s in data.top_stories if s.section == Section.POLITICS)
    assert security.essay == StoryEssay(paragraphs=["Uno.", "Due."], sources_read=2)
    assert politics.essay is None


def test_issues_stored_before_essays_still_load():
    import json

    data = assemble(ISSUE, None, [Story("security", [_cand(1)], "h")], [], {}, 1)
    raw = json.loads(data.model_dump_json())
    for story in raw["top_stories"]:
        del story["essay"]
    assert BulletinData.model_validate(raw).top_stories[0].essay is None


async def test_lead_analyses_and_essays_share_one_opus_concurrency_budget(
    session_factory, monkeypatch
):
    """Both run at once: with analysis_concurrency=1, never two Opus calls in flight."""
    import asyncio

    from feed_brain.config import Settings
    from feed_brain.services import essay as essay_mod
    from feed_brain.services.essay import EssayDraft

    live = peak = 0

    async def opus_call():
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1

    async def fake_deep_analyze(article, client, gemini, settings=None):  # noqa: ARG001
        await opus_call()
        return ANALYSIS

    async def fake_opus(*_args):
        await opus_call()
        return EssayDraft(paragraphs=["P."])

    monkeypatch.setattr(bulletin_mod, "deep_analyze", fake_deep_analyze)
    monkeypatch.setattr(bulletin_mod, "write_essays", essay_mod.write_essays)  # the real one
    monkeypatch.setattr(essay_mod, "opus_structured", fake_opus)
    await _seed(session_factory)
    settings = Settings(_env_file=None, analysis_concurrency=1)

    bulletin = await generate_bulletin(
        ISSUE, gemini=FakeGemini(), anthropic=object(), settings=settings
    )

    data = BulletinData.model_validate_json(bulletin.content_json)
    assert all(s.essay for s in data.top_stories)  # 2 essays + 2 analyses ran
    assert peak == 1
