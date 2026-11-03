# ABOUTME: Renders bulletins and weekly recaps to HTML (page + email body) and Markdown (vault note).
# ABOUTME: Templates live in feed_brain/templates; HTML is autoescaped, Markdown is not.

from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, PackageLoader, select_autoescape

from feed_brain.models import SECTION_TITLES, BulletinData, WeeklyData
from feed_brain.services.bulletin import italian_date


def paragraphs(text: str) -> list[str]:
    """Blank-line separated paragraphs."""
    return [p.strip() for p in (text or "").split("\n\n") if p.strip()]


def safe_url(url: str) -> str:
    """Only http(s) links reach the page; feed-supplied javascript:/data: URLs become '#'."""
    return url if urlsplit(url or "").scheme in {"http", "https"} else "#"


def md_text(text: str) -> str:
    """Markdown link label: one line, brackets escaped."""
    return " ".join((text or "").split()).replace("[", "\\[").replace("]", "\\]")


def md_url(url: str) -> str:
    """Markdown link target: http(s) only, parentheses and spaces percent-encoded."""
    return safe_url(url).replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def md_link(label: str, url: str) -> str:
    """Markdown link, or the bare label when the target is no web page (newsletter digest)."""
    target = md_url(url)
    return f"[{md_text(label)}]({target})" if target != "#" else md_text(label)


def folded(count: int) -> str:
    """Closing line of an 'In breve' section that hides `count` stories."""
    return "e un'altra storia" if count == 1 else f"e altre {count} storie"


_env = Environment(
    loader=PackageLoader("feed_brain", "templates"),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_env.filters["paragraphs"] = paragraphs
_env.filters["safe_url"] = safe_url
_env.filters["md_text"] = md_text
_env.filters["md_url"] = md_url
_env.filters["md_link"] = md_link
_env.filters["folded"] = folded


def render_bulletin_html(
    data: BulletinData,
    site_url: str | None = None,
    *,
    public: bool = False,
    votes: Callable[[str], dict[str, str]] | None = None,
) -> str:
    """Single-column, inline-styled page that also works as an email body.

    public=True is the site variant: stories backed by paid sources keep title and link only.
    votes (email only, never on the public page) maps a story's first source URL to its
    signed 👍/👎 links.
    """
    day = italian_date(data.issue_date)
    return _env.get_template("bulletin.html").render(
        data=data,
        section_titles=SECTION_TITLES,
        page_title=f"{data.title} · {day}",
        kicker=f"Bollettino · {day}",
        site_url=site_url,
        public=public,
        votes=None if public else votes,
    )


def render_bulletin_md(data: BulletinData) -> str:
    """Obsidian note with frontmatter."""
    return _env.get_template("bulletin.md").render(data=data, section_titles=SECTION_TITLES)


def write_bulletin_files(
    data: BulletinData, out_dir: Path, site_url: str | None = None
) -> tuple[Path, Path]:
    """Write <out_dir>/bulletins/<date>.html and .md; returns both paths."""
    folder = out_dir / "bulletins"
    folder.mkdir(parents=True, exist_ok=True)
    stem = data.issue_date.isoformat()
    html_path = folder / f"{stem}.html"
    md_path = folder / f"{stem}.md"
    html_path.write_text(render_bulletin_html(data, site_url), encoding="utf-8")
    md_path.write_text(render_bulletin_md(data), encoding="utf-8")
    return html_path, md_path


def render_weekly_html(
    data: WeeklyData, site_url: str | None = None, *, public: bool = False
) -> str:
    """Weekly recap page/email; public=True hides arc text backed by paid sources."""
    title_range = f"{italian_date(data.week_start)} - {italian_date(data.week_end)}"
    return _env.get_template("weekly.html").render(
        data=data,
        page_title=f"{data.title} · {title_range}",
        kicker=f"Settimana · {title_range}",
        site_url=site_url,
        public=public,
    )


def render_weekly_md(data: WeeklyData) -> str:
    return _env.get_template("weekly.md").render(data=data)


def iso_week_label(data: WeeklyData) -> str:
    """'2026-W44'."""
    year, week, _ = data.week_start.isocalendar()
    return f"{year}-W{week:02d}"


def write_weekly_files(
    data: WeeklyData, out_dir: Path, site_url: str | None = None
) -> tuple[Path, Path]:
    """Write <out_dir>/weekly/<YYYY-Www>.html and .md; returns both paths."""
    folder = out_dir / "weekly"
    folder.mkdir(parents=True, exist_ok=True)
    stem = iso_week_label(data)
    html_path = folder / f"{stem}.html"
    md_path = folder / f"{stem}.md"
    html_path.write_text(render_weekly_html(data, site_url), encoding="utf-8")
    md_path.write_text(render_weekly_md(data), encoding="utf-8")
    return html_path, md_path


def render_index_html(bulletins: list[tuple[date, str]], weeklies: list[tuple[date, str]]) -> str:
    """Archive page; inputs are (issue_date, title) and (week_start, title), newest first."""
    weekly_rows = []
    for start, title in weeklies:
        year, week, _ = start.isocalendar()
        weekly_rows.append({"label": f"{year}-W{week:02d}", "title": title})
    return _env.get_template("index.html").render(
        bulletins=[{"issue_date": d, "title": t} for d, t in bulletins],
        weeklies=weekly_rows,
        page_title="feed-brain",
        kicker="Archivio",
    )


def render_vote_page(
    *, state: str, message: str, headline: str = "", thumb: str = "", undo_url: str = ""
) -> str:
    """Page of the vote service: pending (its script casts the vote), recorded, undone, error."""
    return _env.get_template("vote.html").render(
        page_title="feed-brain · voto",
        kicker="feed-brain · voto",
        state=state,
        message=message,
        headline=headline,
        thumb=thumb,
        undo_url=undo_url,
    )
