# ABOUTME: Cloud side of the vault bridge: queues digest notes and Second Brain entries on storage.
# ABOUTME: Entries: top-story leads with high tier and confidence >= auto_threshold, once per article.

import json
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update

from feed_brain.config import Settings
from feed_brain.db.models import Article
from feed_brain.db.session import get_session_factory
from feed_brain.models import ArticleStatus, Tier
from feed_brain.services.integrator import build_entry, build_timeline_entry, topic_for
from feed_brain.services.storage import Storage

log = structlog.get_logger()

PENDING = "vault-outbox/pending/"
PROCESSED = "vault-outbox/processed/"
REJECTED = "vault-outbox/rejected/"
DIGESTS = f"{PENDING}digests/"
ENTRIES = f"{PENDING}entries/"


def queue_digest(store: Storage, name: str, markdown: str) -> str:
    """Queue a vault note (bulletin or weekly) for `<vault>/Digests/<name>.md`."""
    path = f"{DIGESTS}{name}.md"
    store.write_text(path, markdown, "text/markdown; charset=utf-8")
    return path


async def queue_entries(
    store: Storage, bulletin_id: int, lead_urls: set[str], settings: Settings, record: bool
) -> int:
    """Queue Second Brain entries for the bulletin's top-story leads; returns how many.

    Only leads with tier high and confidence >= auto_threshold qualify (at most top_stories
    per day). With record, articles are marked integrated (target = topic) so they are
    queued once.
    """
    if not lead_urls:
        return 0
    factory = get_session_factory()
    async with factory() as session:
        articles = (
            (
                await session.execute(
                    select(Article).where(
                        Article.bulletin_id == bulletin_id,
                        Article.url.in_(lead_urls),
                        Article.tier == Tier.HIGH,
                        Article.confidence >= settings.auto_threshold,
                        Article.integrated_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for article in articles:
            topic = topic_for(article.category)
            payload = {
                "article_id": article.id,
                "url": article.url,
                "title": article.title,
                "topic": topic,
                "entry": build_entry(article),
                "timeline": build_timeline_entry(article, topic),
            }
            store.write_text(
                f"{ENTRIES}{article.id}.json",
                json.dumps(payload, ensure_ascii=False),
                "application/json",
            )
            if record:
                await session.execute(
                    update(Article)
                    .where(Article.id == article.id)
                    .values(
                        integrated_at=datetime.now(UTC),
                        integration_target=topic,
                        status=ArticleStatus.INTEGRATED,
                    )
                )
        await session.commit()
    log.info("outbox_entries_queued", bulletin_id=bulletin_id, entries=len(articles))
    return len(articles)
