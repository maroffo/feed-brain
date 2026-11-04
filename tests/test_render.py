# ABOUTME: Tests for bulletin rendering to HTML (email/web) and Markdown (vault note).
# ABOUTME: Checks structure, escaping and that the files land under site/bulletins/<date>.

from datetime import date

from feed_brain.models import (
    BriefItem,
    BulletinData,
    DeepAnalysis,
    Section,
    SectionBlock,
    SourceRef,
    TopStory,
)
from feed_brain.services.feedback import StoryLinks
from feed_brain.services.render import (
    render_bulletin_html,
    render_bulletin_md,
    write_bulletin_files,
)

DATA = BulletinData(
    issue_date=date(2026, 11, 2),
    title="Ransomware e <script>",
    key_points=["Primo fatto del giorno.", "Secondo fatto."],
    top_stories=[
        TopStory(
            title="Breccia in Italia",
            text="Secondo Krebs, ...",
            section=Section.SECURITY,
            sources=[
                SourceRef(name="Krebs", title="Big breach", url="https://krebs/a"),
                SourceRef(name="Il Post", title="Attacco", url="https://post/b"),
            ],
            analysis=DeepAnalysis(
                summary="Analisi lunga.",
                insights=["Insight uno"],
                money_quote="The quote.",
                actionables=["Verifica i backup"],
            ),
        )
    ],
    sections=[
        SectionBlock(
            key=Section.SPORT,
            title="Sport",
            items=[
                BriefItem(
                    headline="Rugby: Italia batte Francia",
                    summary="Partita storica.",
                    sources=[SourceRef(name="On Rugby", title="Italia", url="https://onrugby/x")],
                )
            ],
        )
    ],
    articles_count=42,
)


def _brief_block(headline: str, url: str) -> SectionBlock:
    source = SourceRef(name="Feed", title=headline, url=url)
    return SectionBlock(
        key=Section.TECH,
        title="Tech",
        items=[BriefItem(headline=headline, summary="", sources=[source])],
    )


def test_html_contains_all_parts_and_escapes():
    html = render_bulletin_html(DATA)
    assert "2 novembre 2026" in html
    assert "Ransomware e &lt;script&gt;" in html and "<script>" not in html
    assert "<li>Primo fatto del giorno.</li>" in html and "<li>Secondo fatto.</li>" in html
    assert 'href="https://krebs/a"' in html and "Il Post" in html
    assert "The quote." in html and "Insight uno" in html and "Verifica i backup" in html
    # the analysis summary feeds the writer, the issue does not repeat it
    assert "Analisi lunga." not in html
    assert "Sport" in html and "Rugby: Italia batte Francia" in html
    assert "Radar minacce" not in html
    assert '<meta name="robots" content="noindex' in html
    assert "42 articoli" in html


def test_markdown_note_has_frontmatter_and_links():
    md = render_bulletin_md(DATA)
    assert md.startswith("---\n")
    assert "date: 2026-11-02" in md
    assert "# Ransomware e <script>" in md
    assert "[Big breach](https://krebs/a)" in md
    assert "## Il punto\n\n- Primo fatto del giorno.\n- Secondo fatto.\n" in md
    assert "> The quote." in md and "- Insight uno" in md and "- Verifica i backup" in md
    assert "Analisi lunga." not in md
    assert "## Sport" in md and "Radar minacce" not in md


def test_write_bulletin_files(tmp_path):
    html_path, md_path = write_bulletin_files(DATA, tmp_path)
    assert html_path == tmp_path / "bulletins" / "2026-11-02.html"
    assert md_path == tmp_path / "bulletins" / "2026-11-02.md"
    assert "Breccia in Italia" in html_path.read_text(encoding="utf-8")
    assert "Breccia in Italia" in md_path.read_text(encoding="utf-8")


def test_safe_url_blocks_non_http_schemes():
    from feed_brain.services.render import safe_url

    assert safe_url("https://ok/x") == "https://ok/x"
    assert safe_url("http://ok/x") == "http://ok/x"
    assert safe_url("javascript:alert(1)") == "#"
    assert safe_url("data:text/html,x") == "#"
    assert safe_url("") == "#"


def test_html_neutralizes_javascript_links_from_feeds():
    evil = DATA.model_copy(update={"sections": [_brief_block("x", "javascript:alert(1)")]})
    html = render_bulletin_html(evil)
    assert "javascript:" not in html


def test_weekly_render_and_files(tmp_path):
    from feed_brain.models import BulletinRef, WeeklyArcData, WeeklyData
    from feed_brain.services.render import render_weekly_html, write_weekly_files

    weekly = WeeklyData(
        week_start=date(2026, 10, 26),
        week_end=date(2026, 11, 1),
        title="La settimana <b>",
        subtitle="Sotto",
        intro="Intro uno.\n\nIntro due.",
        arcs=[
            WeeklyArcData(
                title="Arco",
                text="Racconto.",
                links=[SourceRef(name="Krebs", title="T", url="https://k/1")],
            )
        ],
        reflection="Riflessione.",
        bulletins=[BulletinRef(issue_date=date(2026, 10, 26), title="Lunedi")],
        stories_count=10,
    )
    html = render_weekly_html(weekly, site_url="https://site")
    assert "La settimana &lt;b&gt;" in html and "Intro due." in html
    assert 'href="https://site/bulletins/2026-10-26.html"' in html
    assert "Da tenere d" in html and "26 ottobre 2026 - 1 novembre 2026" in html
    html_path, md_path = write_weekly_files(weekly, tmp_path)
    assert html_path.name == "2026-W44.html"
    assert "[[feed-brain 2026-10-26]]" in md_path.read_text(encoding="utf-8")


def test_markdown_links_are_escaped():
    from feed_brain.services.render import md_text, md_url

    assert md_text("A [B]\nC") == "A \\[B\\] C"
    assert md_url("https://w/Foo_(bar) x") == "https://w/Foo_%28bar%29%20x"
    assert md_url("javascript:alert(1)") == "#"
    tricky = DATA.model_copy(
        update={"sections": [_brief_block("Evil [x](y)", "https://w/Foo_(bar)")]}
    )
    assert "[Evil \\[x\\](y)](https://w/Foo_%28bar%29)" in render_bulletin_md(tricky)


def test_weekly_links_are_relative_without_site_url():
    from feed_brain.models import BulletinRef, WeeklyData
    from feed_brain.services.render import render_weekly_html

    weekly = WeeklyData(
        week_start=date(2026, 10, 26),
        week_end=date(2026, 11, 1),
        title="W",
        subtitle="",
        intro="",
        arcs=[],
        reflection="",
        bulletins=[BulletinRef(issue_date=date(2026, 10, 26), title="L")],
        stories_count=0,
    )
    assert 'href="../bulletins/2026-10-26.html"' in render_weekly_html(weekly)


PAID = SourceRef(
    name="davidcarretta.substack.com",
    title="Polonia",
    url="https://davidcarretta.substack.com/p/polonia",
    paid=True,
)
PAID_DATA = DATA.model_copy(
    update={
        "top_stories": [
            TopStory(
                title="Golpe in Polonia",
                text="Testo tratto dal post a pagamento.",
                section=Section.POLITICS,
                sources=[PAID],
                analysis=DeepAnalysis(
                    summary="Analisi riservata.",
                    insights=["Insight riservato"],
                    money_quote="Citazione riservata.",
                    actionables=["Azione riservata"],
                ),
            ),
            DATA.top_stories[0],
        ],
        "sections": [
            SectionBlock(
                key=Section.POLITICS,
                title="Politica",
                items=[
                    BriefItem(
                        headline="Breve riservata", summary="Sintesi riservata.", sources=[PAID]
                    )
                ],
            )
        ],
    }
)
PAID_TEXT = (
    "Testo tratto dal post a pagamento",
    "Insight riservato",
    "Citazione riservata",
    "Azione riservata",
    "Sintesi riservata",
)


def test_public_page_shows_only_title_and_link_for_paid_sources():
    html = render_bulletin_html(PAID_DATA, public=True)
    assert "Golpe in Polonia" in html and "Breve riservata" in html
    assert 'href="https://davidcarretta.substack.com/p/polonia"' in html
    assert [t for t in PAID_TEXT if t in html] == []
    assert "abbonati" in html
    # free stories in the same issue keep their text and analysis
    assert "Secondo Krebs" in html and "The quote." in html


def test_email_keeps_the_paid_content():
    html = render_bulletin_html(PAID_DATA)
    assert [t for t in PAID_TEXT if t not in html] == []


def test_sources_without_a_web_page_render_as_plain_text():
    digest = SourceRef(
        name="Newsletter", title="Weekly digest", url="mid:abc@mail.gmail.com", paid=True
    )
    data = DATA.model_copy(
        update={
            "sections": [
                SectionBlock(
                    key=Section.TECH,
                    title="Tech",
                    items=[
                        BriefItem(headline="Digest della settimana", summary="", sources=[digest])
                    ],
                )
            ],
        }
    )
    html = render_bulletin_html(data)
    assert "Digest della settimana" in html and "Newsletter" in html
    assert "mid:" not in html and 'href="#"' not in html


def test_weekly_public_page_hides_arc_text_backed_by_paid_sources():
    from feed_brain.models import BulletinRef, WeeklyArcData, WeeklyData
    from feed_brain.services.render import render_weekly_html

    weekly = WeeklyData(
        week_start=date(2026, 10, 26),
        week_end=date(2026, 11, 1),
        title="La settimana",
        subtitle="",
        intro="Intro.",
        arcs=[
            WeeklyArcData(title="Arco riservato", text="Racconto riservato.", links=[PAID]),
            WeeklyArcData(
                title="Arco libero",
                text="Racconto libero.",
                links=[SourceRef(name="Krebs", title="T", url="https://k/1")],
            ),
        ],
        reflection="",
        bulletins=[BulletinRef(issue_date=date(2026, 10, 26), title="L")],
        stories_count=2,
    )
    public = render_weekly_html(weekly, public=True)
    assert "Arco riservato" in public and "Racconto riservato" not in public
    assert "Racconto libero" in public
    assert "Racconto riservato" in render_weekly_html(weekly)


def test_single_source_brief_names_its_source_without_repeating_the_link():
    """The headline already links the article: a single-source brief adds only the outlet name
    (keeps the email under Gmail's clip size); multi-source briefs keep the full source list."""
    multi = BriefItem(
        headline="Due fonti",
        summary="",
        sources=[
            SourceRef(name="Krebs", title="A", url="https://krebs/x"),
            SourceRef(name="Il Post", title="B", url="https://post/y"),
        ],
    )
    data = DATA.model_copy(
        update={
            "sections": [
                SectionBlock(
                    key=Section.SPORT, title="Sport", items=[DATA.sections[0].items[0], multi]
                )
            ]
        }
    )
    html = render_bulletin_html(data)
    assert html.count('href="https://onrugby/x"') == 1
    assert "On Rugby" in html and "On Rugby: Italia" not in html
    assert "Krebs: A" in html and "Il Post: B" in html


def test_notes_appear_only_when_the_story_carries_them():
    quote_only = DeepAnalysis(
        summary="S.", insights=[], money_quote="Solo la citazione.", actionables=[]
    )
    story = DATA.top_stories[0].model_copy(update={"analysis": quote_only})
    html = render_bulletin_html(DATA.model_copy(update={"top_stories": [story]}))
    assert "Solo la citazione." in html
    assert "Da fare" not in html and "Approfondimento" not in html


def test_issues_stored_with_a_prose_editorial_still_render_it():
    """The first issues carry 'Il punto' as prose paragraphs instead of key points."""
    legacy = DATA.model_copy(
        update={
            "key_points": [],
            "subtitle": "Una giornata",
            "editorial": "Primo paragrafo.\n\nSecondo paragrafo.",
        }
    )
    html = render_bulletin_html(legacy)
    assert "<p>Primo paragrafo.</p>" in html and "<p>Secondo paragrafo.</p>" in html
    assert "Una giornata" in html
    assert "Primo paragrafo." in render_bulletin_md(legacy)


LEGACY_JSON = """{
  "issue_date": "2026-11-02",
  "title": "Bollettino del 2 novembre",
  "subtitle": "Una frase sul filo della giornata.",
  "editorial": "Primo paragrafo.\\n\\nSecondo paragrafo.",
  "top_stories": [{
    "title": "Storia", "text": "Testo.", "section": "security",
    "sources": [{"name": "Krebs", "title": "T", "url": "https://k/1", "paid": false}],
    "analysis": {"summary": "Sintesi.", "insights": ["i1", "i2", "i3", "i4"],
                 "money_quote": "Quote.", "actionables": ["a1"]},
    "lead_url": "https://k/1"
  }],
  "sections": [{"key": "management", "title": "Management e agile", "omitted": 0,
                "items": [{"headline": "Scrum", "summary": "S.",
                           "sources": [{"name": "Blog", "title": "B", "url": "https://b/1"}]}]}],
  "radar": [{"title": "CVE-1", "url": "https://cve/1", "source": "cvefeed"}],
  "articles_count": 50
}"""


def test_issues_stored_before_the_key_points_still_parse_and_render():
    """Stored JSON of the first issues: subtitle, prose editorial, radar, a management block."""
    data = BulletinData.model_validate_json(LEGACY_JSON)
    assert (data.key_points, data.subtitle) == ([], "Una frase sul filo della giornata.")
    html = render_bulletin_html(data)
    assert "<p>Secondo paragrafo.</p>" in html and "Management e agile" in html
    assert "Quote." in html and "CVE-1" not in html
    assert "Secondo paragrafo." in render_bulletin_md(data)


def test_money_quote_is_styled_outside_the_notes_box():
    css = render_bulletin_html(DATA).split("</style>", 1)[0]
    assert ".story blockquote{" in css


def _two_brief_issue(omitted: int) -> BulletinData:
    block = _brief_block("Primo breve", "https://a/1")
    second = BriefItem(
        headline="Secondo breve",
        summary="Sintesi.",
        sources=[
            SourceRef(name="Uno", title="x", url="https://b/1"),
            SourceRef(name="Due", title="y", url="https://b/2"),
        ],
    )
    block = block.model_copy(update={"items": [*block.items, second], "omitted": omitted})
    return DATA.model_copy(update={"sections": [block, _brief_block("Altro", "https://c/1")]})


def test_markdown_briefs_are_one_list_item_per_line_and_headings_stand_apart():
    md = render_bulletin_md(_two_brief_issue(omitted=0))
    assert "\n- **[Primo breve](https://a/1)** \n- **[Secondo breve](https://b/1)**" in md
    assert "· [Due](https://b/2)\n\n## Tech\n" in md


def test_a_single_folded_story_is_named_in_the_singular():
    md = render_bulletin_md(_two_brief_issue(omitted=1))
    html = render_bulletin_html(_two_brief_issue(omitted=1))
    assert "e un'altra storia" in md and "e un&#39;altra storia" in html
    assert "altre 1 storie" not in md + html
    assert "e altre 4 storie" in render_bulletin_md(_two_brief_issue(omitted=4))


def _links():
    return StoryLinks("https://fb.run.app", b"k" * 32, DATA.issue_date)


def test_email_carries_quiet_vote_links_on_top_stories_and_briefs():
    links = _links()
    html = render_bulletin_html(DATA, links=links)
    top, brief = links.votes("https://krebs/a"), links.votes("https://onrugby/x")
    # top story: a closing line in words; brief: a compact + and minus at the end of the line
    assert f'<a href="{top["up"]}" title="Mi interessa">più così</a>' in html
    assert f'<a href="{top["down"]}" title="Non mi interessa">meno così</a>' in html
    assert f'<a href="{brief["up"]}" title="Più così">+</a>' in html
    assert f'<a href="{brief["down"]}" title="Meno così">−</a>' in html
    assert "👍" not in html and "👎" not in html
    # the story's line comes after its notes, not in the kicker above the title
    story = html[html.index("Breccia in Italia") :]
    assert story.index("Verifica i backup") < story.index("più così")


def test_brief_votes_end_the_text_line_even_when_sources_follow_on_their_own_line():
    issue = _two_brief_issue(omitted=0)
    links = StoryLinks("https://fb.run.app", b"k" * 32, issue.issue_date)
    html = render_bulletin_html(issue, links=links)
    item = html[html.index("Secondo breve") :]
    assert item.index(links.votes("https://b/1")["down"]) < item.index('<div class="src">')


def test_no_vote_links_without_vote_links_on_the_public_page_or_in_the_vault_note():
    assert 'class="vote' not in render_bulletin_html(DATA)
    assert 'class="vote' not in render_bulletin_html(DATA, links=_links(), public=True)
    assert "più così" not in render_bulletin_md(DATA)


def test_the_archive_link_opens_the_index_page_not_the_bucket_listing():
    from feed_brain.models import WeeklyData
    from feed_brain.services.render import render_weekly_html

    weekly = WeeklyData(
        week_start=date(2026, 10, 26),
        week_end=date(2026, 11, 1),
        title="W",
        subtitle="",
        intro="",
        arcs=[],
        reflection="",
        bulletins=[],
        stories_count=0,
    )
    for html in (
        render_bulletin_html(DATA, "https://site"),
        render_weekly_html(weekly, "https://site"),
    ):
        assert '<a href="https://site/index.html">archivio</a>' in html


def _with_essay(data: BulletinData) -> BulletinData:
    from feed_brain.models import StoryEssay

    story = data.top_stories[0].model_copy(
        update={"essay": StoryEssay(paragraphs=["Uno."], sources_read=2)}
    )
    return data.model_copy(update={"top_stories": [story]})


def test_the_email_links_the_essay_of_a_top_story_that_has_one():
    links = _links()
    html = render_bulletin_html(_with_essay(DATA), links=links)
    assert f'href="{links.essay("https://krebs/a")}"' in html
    assert "Il quadro completo: cosa dicono le 2 fonti" in html
    # no essay, no link
    assert "/e/" not in render_bulletin_html(DATA, links=links)


def test_no_essay_link_on_the_public_page_without_links_or_in_the_vault_note():
    with_essay = _with_essay(DATA)
    assert "/e/" not in render_bulletin_html(with_essay, links=_links(), public=True)
    assert "Il quadro completo" not in render_bulletin_html(with_essay)
    assert "Il quadro completo" not in render_bulletin_md(with_essay)
