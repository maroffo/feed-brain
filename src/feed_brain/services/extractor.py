# ABOUTME: Content extraction service using readability-lxml.
# ABOUTME: Downloads article HTML and extracts clean text content.

import httpx
import structlog
from bs4 import BeautifulSoup
from readability import Document

from feed_brain.config import Settings, get_settings

log = structlog.get_logger()


async def extract_content(url: str, settings: Settings | None = None) -> str | None:
    """Download and extract the main text content from a URL.

    Returns extracted text, or None if extraction failed.
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
        soup = BeautifulSoup(html_content, "html.parser")
        text = soup.get_text(separator="\n").strip()

        if len(text) < 50:
            log.warning("extraction_too_short", url=url, length=len(text))
            return None

        log.info("content_extracted", url=url, length=len(text))
        return text

    except httpx.HTTPError as e:
        log.error("extraction_http_error", url=url, error=str(e))
        return None
    except Exception as e:
        log.error("extraction_error", url=url, error=str(e))
        return None
