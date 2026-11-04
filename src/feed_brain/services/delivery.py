# ABOUTME: Delivers a stored issue: publishes the page + archive index, emails it once, queues vault notes.
# ABOUTME: published_at / sent_at make job retries safe; record=False (dry run) writes files but no DB state.

from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update

from feed_brain.config import Settings, get_settings
from feed_brain.db.models import Bulletin, WeeklyDigest
from feed_brain.db.session import get_session_factory
from feed_brain.models import BulletinData, WeeklyData, edition_for
from feed_brain.services.bulletin import italian_date
from feed_brain.services.feedback import StoryLinks, link_secret
from feed_brain.services.mailer import send_email
from feed_brain.services.outbox import queue_digest, queue_entries
from feed_brain.services.render import (
    iso_week_label,
    render_bulletin_html,
    render_bulletin_md,
    render_index_html,
    render_weekly_html,
    render_weekly_md,
)
from feed_brain.services.storage import Storage

log = structlog.get_logger()

HTML = "text/html; charset=utf-8"
ROBOTS = "User-agent: *\nDisallow: /\n"
INDEX_BULLETINS = 90
INDEX_WEEKLIES = 26


def story_links(data: BulletinData, settings: Settings) -> StoryLinks | None:
    """Signed vote and essay links for the email, or None when the feedback service is not
    configured."""
    secret = link_secret(settings)
    if not settings.feedback_url or secret is None:
        return None
    return StoryLinks(settings.feedback_url, secret, data.issue_date)


async def publish_index(site: Storage) -> None:
    """Regenerate index.html (recent issues) and robots.txt on the site."""
    async with get_session_factory()() as session:
        bulletins = (
            await session.execute(
                select(Bulletin.issue_date, Bulletin.edition, Bulletin.title)
                .order_by(Bulletin.issue_date.desc(), Bulletin.id)
                .limit(INDEX_BULLETINS)
            )
        ).all()
        weeklies = (
            await session.execute(
                select(WeeklyDigest.week_start, WeeklyDigest.title)
                .order_by(WeeklyDigest.week_start.desc())
                .limit(INDEX_WEEKLIES)
            )
        ).all()
    site.write_text(
        "index.html",
        render_index_html([tuple(b) for b in bulletins], [tuple(w) for w in weeklies]),
        HTML,
    )
    site.write_text("robots.txt", ROBOTS, "text/plain; charset=utf-8")


async def _stamp(
    model: type[Bulletin] | type[WeeklyDigest], row_id: int, **values: datetime
) -> None:
    async with get_session_factory()() as session:
        await session.execute(update(model).where(model.id == row_id).values(**values))
        await session.commit()


async def deliver_bulletin(
    bulletin_id: int,
    *,
    site: Storage,
    outbox: Storage,
    send: bool,
    record: bool,
    to: str | None = None,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    async with get_session_factory()() as session:
        row = await session.get(Bulletin, bulletin_id)
    if row is None:
        raise ValueError(f"no bulletin {bulletin_id}")
    data = BulletinData.model_validate_json(row.content_json)
    edition = edition_for(data.edition)
    html = render_bulletin_html(data, settings.site_url, links=story_links(data, settings))
    md = render_bulletin_md(data)
    day = data.issue_date.isoformat()

    if row.published_at is None or not record:
        page = render_bulletin_html(data, settings.site_url, public=True)
        site.write_text(f"bulletins/{edition.stem(data.issue_date)}.html", page, HTML)
        await publish_index(site)
        if record:
            await _stamp(Bulletin, bulletin_id, published_at=datetime.now(UTC))
        log.info("bulletin_published", issue_date=day, edition=edition.key)

    # Vault before email: a mail failure must not hold back the notes.
    queue_digest(outbox, edition.note_name(data.issue_date), md)
    lead_urls = {story.lead_url for story in data.top_stories if story.lead_url}
    await queue_entries(outbox, bulletin_id, lead_urls, settings, record)

    if send and row.sent_at is None:
        subject = f"{edition.title} del {italian_date(data.issue_date)}: {data.title}"
        await send_email(subject, html, md, to=to, settings=settings)
        if record:
            await _stamp(Bulletin, bulletin_id, sent_at=datetime.now(UTC))


async def deliver_weekly(
    digest_id: int,
    *,
    site: Storage,
    outbox: Storage,
    send: bool,
    record: bool,
    to: str | None = None,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    async with get_session_factory()() as session:
        row = await session.get(WeeklyDigest, digest_id)
    if row is None:
        raise ValueError(f"no weekly digest {digest_id}")
    data = WeeklyData.model_validate_json(row.content_json)
    html = render_weekly_html(data, settings.site_url)
    md = render_weekly_md(data)
    label = iso_week_label(data)

    if row.published_at is None or not record:
        page = render_weekly_html(data, settings.site_url, public=True)
        site.write_text(f"weekly/{label}.html", page, HTML)
        await publish_index(site)
        if record:
            await _stamp(WeeklyDigest, digest_id, published_at=datetime.now(UTC))
        log.info("weekly_published", week=label)

    queue_digest(outbox, f"feed-brain {label}", md)

    if send and row.sent_at is None:
        await send_email(f"La settimana {label}: {data.title}", html, md, to=to, settings=settings)
        if record:
            await _stamp(WeeklyDigest, digest_id, sent_at=datetime.now(UTC))
