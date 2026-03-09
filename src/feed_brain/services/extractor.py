# ABOUTME: Content extraction service with PDF routing and trafilatura fallback.
# ABOUTME: Downloads article HTML, extracts via readability-lxml, falls back to trafilatura.

import asyncio

import httpx
import structlog
from bs4 import BeautifulSoup
from readability import Document

from feed_brain.config import Settings, get_settings
from feed_brain.services.pdf import extract_pdf, is_pdf_url

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


def _sanitize_html(html_content: str) -> str | None:
    """Sanitize HTML, keeping only safe tags and attributes."""
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
    text_length = len(soup.get_text().strip())

    if text_length < 50:
        return None

    return cleaned


def _extract_with_readability(html: str) -> str | None:
    """CPU-bound readability extraction (run in thread)."""
    doc = Document(html)
    html_content = doc.summary()
    return _sanitize_html(html_content)


def _extract_with_trafilatura(html: str) -> str | None:
    """CPU-bound trafilatura extraction (run in thread)."""
    import trafilatura

    text = trafilatura.extract(html)
    if text and len(text) >= 50:
        return text
    return None


async def extract_content(url: str, settings: Settings | None = None) -> str | None:
    """Download and extract content from a URL.

    Routes PDFs to PyMuPDF extraction. For HTML, tries readability-lxml first,
    falls back to trafilatura on the already-downloaded HTML.
    CPU-bound parsing is offloaded to threads to avoid blocking the event loop.
    Returns cleaned content or None if extraction failed.
    """
    settings = settings or get_settings()

    # Route PDFs to dedicated extractor
    if is_pdf_url(url):
        return await extract_pdf(url, settings)

    try:
        async with httpx.AsyncClient(
            timeout=settings.feed_timeout,
            headers={"User-Agent": settings.feed_user_agent},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        html = response.text

        # Try readability-lxml first (CPU-bound, offload to thread)
        cleaned = await asyncio.to_thread(_extract_with_readability, html)
        if cleaned:
            log.info("content_extracted", url=url, method="readability")
            return cleaned

        # Fallback to trafilatura on same HTML (CPU-bound, offload to thread)
        text = await asyncio.to_thread(_extract_with_trafilatura, html)
        if text:
            log.info("content_extracted", url=url, method="trafilatura")
            return text

        log.warning("extraction_failed_both", url=url)
        return None

    except httpx.HTTPError as e:
        log.error("extraction_http_error", url=url, error=str(e))
        return None
    except Exception as e:
        log.error("extraction_error", url=url, error=str(e))
        return None
