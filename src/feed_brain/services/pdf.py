# ABOUTME: PDF content extraction using PyMuPDF.
# ABOUTME: Handles arxiv URL detection/conversion and two-column paper extraction.

import re
import tempfile
from pathlib import Path

import httpx
import pymupdf
import structlog

from feed_brain.config import Settings, get_settings

log = structlog.get_logger()

ARXIV_ABS_PATTERN = re.compile(r"https?://arxiv\.org/abs/(\d+\.\d+)")


def is_pdf_url(url: str) -> bool:
    """Check if a URL points to a PDF (arxiv or .pdf extension)."""
    return url.lower().endswith(".pdf") or bool(ARXIV_ABS_PATTERN.match(url))


def to_pdf_url(url: str) -> str:
    """Convert arxiv /abs/ URLs to /pdf/ URLs. Pass through others."""
    match = ARXIV_ABS_PATTERN.match(url)
    if match:
        return f"https://arxiv.org/pdf/{match.group(1)}"
    return url


async def extract_pdf(url: str, settings: Settings | None = None) -> str | None:
    """Download and extract text from a PDF URL.

    Uses PyMuPDF with sort=True for proper two-column layout extraction.
    Returns plain text or None if extraction fails.
    """
    settings = settings or get_settings()
    pdf_url = to_pdf_url(url)

    try:
        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": settings.feed_user_agent},
            follow_redirects=True,
        ) as client:
            response = await client.get(pdf_url)
            response.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = Path(tmp.name)

        try:
            doc = pymupdf.open(str(tmp_path))
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text(sort=True))
            doc.close()
            text = "\n\n".join(text_parts).strip()
        finally:
            tmp_path.unlink(missing_ok=True)

        if len(text) < 100:
            log.warning("pdf_extraction_too_short", url=url, length=len(text))
            return None

        log.info("pdf_extracted", url=url, length=len(text))
        return text

    except httpx.HTTPError as e:
        log.error("pdf_download_error", url=url, error=str(e))
        return None
    except Exception as e:
        log.error("pdf_extraction_error", url=url, error=str(e))
        return None
