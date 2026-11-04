# ABOUTME: Content extraction service with PDF routing and trafilatura fallback.
# ABOUTME: Downloads article HTML, extracts via readability-lxml, falls back to trafilatura.

import asyncio

import httpx
import structlog
from bs4 import BeautifulSoup
from readability import Document

from feed_brain.config import Settings, get_settings
from feed_brain.services import http
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


SHORT_TEXT_CHARS = 1500  # below this, readability may have kept only a teaser: try trafilatura too
_NOT_PAGES = ("image/", "video/", "audio/", "application/octet-stream", "font/")


def visible_length(content: str | None) -> int:
    """Characters a reader sees. Readability output is HTML (it starts with a tag) and is
    parsed; anything else is plain text and is counted as is, so an article quoting
    '<script>' keeps its length."""
    if not content:
        return 0
    if content.lstrip().startswith("<"):
        return len(BeautifulSoup(content, "html.parser").get_text(" ", strip=True))
    return len(content.strip())


def _best_extraction(html: str) -> tuple[str | None, str]:
    """(content, method). Readability first; when it crashes (lxml rejects control characters)
    or returns little text, trafilatura runs too and the longer text wins."""
    try:
        readable = _extract_with_readability(html)
    except Exception as e:  # noqa: BLE001 - any parser failure falls through to trafilatura
        log.warning("readability_failed", error=str(e)[:120])
        readable = None
    readable_length = visible_length(readable)
    if readable_length >= SHORT_TEXT_CHARS:
        return readable, "readability"
    try:
        fallback = _extract_with_trafilatura(html)
    except Exception as e:  # noqa: BLE001 - keep whatever readability found
        log.warning("trafilatura_failed", error=str(e)[:120])
        fallback = None
    if visible_length(fallback) > readable_length:
        return fallback, "trafilatura"
    return readable, "readability"


def extract_from_html(html: str) -> str | None:
    """Main content of an HTML document already in hand (see _best_extraction).

    CPU-bound: call it from a thread inside async code.
    """
    return _best_extraction(html)[0]


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
        response = await http.fetch(url, settings, kind="page")
        content_type = str(response.headers.get("content-type", "") or "").lower()
        if content_type.startswith(_NOT_PAGES):
            log.warning("extraction_not_a_page", url=url, content_type=content_type[:60])
            return None

        # CPU-bound parsing, offloaded to a thread
        content, method = await asyncio.to_thread(_best_extraction, response.text)
        if content:
            log.info("content_extracted", url=url, method=method)
            return content

        log.warning("extraction_failed_both", url=url)
        return None

    except httpx.HTTPError as e:
        log.error("extraction_http_error", url=url, error=str(e))
        return None
    except Exception as e:
        log.error("extraction_error", url=url, error=str(e))
        return None
