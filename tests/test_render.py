# ABOUTME: Tests for bulletin rendering to HTML (email/web) and Markdown (vault note).
# ABOUTME: Checks structure, escaping and that the files land under site/bulletins/<date>.

from datetime import date

from feed_brain.models import (
    BriefItem,
    BulletinData,
    DeepAnalysis,
    RadarItem,
    Section,
    SectionBlock,
    SourceRef,
    TopStory,
)
from feed_brain.services.render import (
    render_bulletin_html,
    render_bulletin_md,
    write_bulletin_files,
)

DATA = BulletinData(
    issue_date=date(2026, 11, 2),
    title="Ransomware e <script>",
    subtitle="Una giornata intensa",
    editorial="Primo paragrafo.\n\nSecondo paragrafo.",
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
    radar=[
        RadarItem(title="LockBit claims Acme SpA", url="https://ransomfeed/1", source="Ransom Feed")
    ],
    articles_count=42,
)


def test_html_contains_all_parts_and_escapes():
    html = render_bulletin_html(DATA)
    assert "2 novembre 2026" in html
    assert "Ransomware e &lt;script&gt;" in html and "<script>" not in html
    assert html.count("<p") >= 2 and "Secondo paragrafo." in html
    assert 'href="https://krebs/a"' in html and "Il Post" in html
    assert "Approfondimento" in html and "The quote." in html and "Verifica i backup" in html
    assert "Sport" in html and "Rugby: Italia batte Francia" in html
    assert "Radar minacce" in html and "LockBit claims Acme SpA" in html
    assert '<meta name="robots" content="noindex' in html
    assert "42 articoli" in html


def test_markdown_note_has_frontmatter_and_links():
    md = render_bulletin_md(DATA)
    assert md.startswith("---\n")
    assert "date: 2026-11-02" in md
    assert "# Ransomware e <script>" in md
    assert "[Big breach](https://krebs/a)" in md
    assert "> The quote." in md
    assert "## Sport" in md and "## Radar minacce" in md


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
    evil = DATA.model_copy(
        update={"radar": [RadarItem(title="x", url="javascript:alert(1)", source="Evil")]}
    )
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
        update={"radar": [RadarItem(title="Evil [x](y)", url="https://w/Foo_(bar)", source="S")]}
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
