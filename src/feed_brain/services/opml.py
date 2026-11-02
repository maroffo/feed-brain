# ABOUTME: OPML file parser for bulk feed import.
# ABOUTME: Parses feeds (name, URL, enclosing folder) and syncs them into feed_sources.

from dataclasses import dataclass
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from feed_brain.db.models import FeedSource

log = structlog.get_logger()


def parse_opml(content: str) -> list[dict[str, str | None]]:
    """Parse OPML content and extract feed entries.

    Returns list of dicts with 'name', 'url' and 'folder' keys. 'folder' is the
    title of the innermost enclosing outline without an xmlUrl, or None for
    top-level feeds.
    """
    feeds: list[dict[str, str | None]] = []

    try:
        root = ElementTree.fromstring(content)  # noqa: S314
    except ElementTree.ParseError as e:
        log.error("opml_parse_error", error=str(e))
        return feeds

    def _walk(node: Element, folder: str | None) -> None:
        for outline in node.findall("outline"):
            xml_url = outline.get("xmlUrl")
            label = outline.get("title") or outline.get("text")
            if xml_url:
                feeds.append({"name": label or xml_url, "url": xml_url, "folder": folder})
            else:
                _walk(outline, label or folder)

    body = root.find("body")
    _walk(body if body is not None else root, None)

    log.info("opml_parsed", count=len(feeds))
    return feeds


@dataclass
class SyncStats:
    """Outcome of syncing parsed OPML feeds into the feed_sources table."""

    imported: int = 0
    reactivated: int = 0
    deactivated: int = 0


async def sync_feed_sources(
    session: AsyncSession, feeds: list[dict[str, str | None]], deactivate_missing: bool
) -> SyncStats:
    """Upsert parsed OPML feeds by URL: add new ones, refresh folder, reactivate.

    With deactivate_missing, active feeds whose URL is not in the OPML are deactivated.
    The caller commits.
    """
    stats = SyncStats()
    existing = {s.url: s for s in (await session.execute(select(FeedSource))).scalars().all()}
    opml_urls: set[str] = set()

    for feed in feeds:
        url = feed["url"] or ""
        opml_urls.add(url)
        source = existing.get(url)
        if source is None:
            source = FeedSource(name=feed["name"] or url, url=url, folder=feed["folder"])
            session.add(source)
            existing[url] = source
            stats.imported += 1
            continue
        source.folder = feed["folder"]
        if not source.active:
            source.active = True
            stats.reactivated += 1

    if deactivate_missing:
        for url, source in existing.items():
            # Only RSS sources come from the OPML; newsletter inbox sources are not its to retire.
            if url not in opml_urls and source.active and source.feed_type == "rss":
                source.active = False
                stats.deactivated += 1

    return stats
