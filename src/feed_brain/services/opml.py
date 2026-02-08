# ABOUTME: OPML file parser for bulk feed import.
# ABOUTME: Extracts feed names and URLs from standard OPML XML format.

from xml.etree import ElementTree

import structlog

log = structlog.get_logger()


def parse_opml(content: str) -> list[dict[str, str]]:
    """Parse OPML content and extract feed entries.

    Returns list of dicts with 'name' and 'url' keys.
    """
    feeds: list[dict[str, str]] = []

    try:
        root = ElementTree.fromstring(content)  # noqa: S314
    except ElementTree.ParseError as e:
        log.error("opml_parse_error", error=str(e))
        return feeds

    for outline in root.iter("outline"):
        xml_url = outline.get("xmlUrl")
        if not xml_url:
            continue

        name = outline.get("title") or outline.get("text") or xml_url
        feeds.append({"name": name, "url": xml_url})

    log.info("opml_parsed", count=len(feeds))
    return feeds
