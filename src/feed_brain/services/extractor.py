# ABOUTME: Content extraction service using readability-lxml.
# ABOUTME: Downloads article HTML and extracts sanitized HTML content.

import httpx
import structlog
from bs4 import BeautifulSoup
from readability import Document

from feed_brain.config import Settings, get_settings

log = structlog.get_logger()

ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "a",
    "ul",
    "ol",
    "li",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "pre",
    "code",
    "img",
    "figure",
    "figcaption",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
]
ALLOWED_ATTRS = {"a": ["href"], "img": ["src", "alt"]}


async def extract_content(url: str, settings: Settings | None = None) -> str | None:
    """Download and extract sanitized HTML content from a URL.

    Returns cleaned HTML preserving structure (paragraphs, links, images),
    or None if extraction failed.
    """
    settings = settings or get_settings()
    try:
        async with httpx.AsyncClient(
            timeout=settings.feed_timeout,
            headers={"User-Agent": settings.feed_user_agent},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        doc = Document(response.text)
        html_content = doc.summary()

        # Sanitize: keep only safe tags and attributes
        soup = BeautifulSoup(html_content, "html.parser")
        for tag in soup.find_all(True):
            if tag.name not in ALLOWED_TAGS:
                tag.unwrap()
            else:
                allowed = ALLOWED_ATTRS.get(tag.name, [])
                for attr in list(tag.attrs):
                    if attr not in allowed:
                        del tag[attr]

        cleaned = str(soup).strip()
        text_length = soup.get_text().strip()

        if len(text_length) < 50:
            log.warning("extraction_too_short", url=url, length=len(text_length))
            return None

        log.info("content_extracted", url=url, length=len(cleaned))
        return cleaned

    except httpx.HTTPError as e:
        log.error("extraction_http_error", url=url, error=str(e))
        return None
    except Exception as e:
        log.error("extraction_error", url=url, error=str(e))
        return None
