# ABOUTME: Tests for PDF extraction and arxiv URL handling.
# ABOUTME: Verifies URL detection, conversion, and extraction logic.

from unittest.mock import AsyncMock, MagicMock, patch

from feed_brain.config import Settings
from feed_brain.services.pdf import extract_pdf, is_pdf_url, to_pdf_url


def test_is_pdf_url_with_pdf_extension():
    """URLs ending in .pdf are detected."""
    assert is_pdf_url("https://example.com/paper.pdf") is True
    assert is_pdf_url("https://example.com/paper.PDF") is True


def test_is_pdf_url_with_arxiv_abs():
    """Arxiv /abs/ URLs are detected as PDFs."""
    assert is_pdf_url("https://arxiv.org/abs/2401.12345") is True


def test_is_pdf_url_with_regular_url():
    """Regular HTML URLs are not detected as PDFs."""
    assert is_pdf_url("https://example.com/article") is False
    assert is_pdf_url("https://example.com/page.html") is False


def test_to_pdf_url_converts_arxiv():
    """Arxiv /abs/ URLs are converted to /pdf/ URLs."""
    assert to_pdf_url("https://arxiv.org/abs/2401.12345") == "https://arxiv.org/pdf/2401.12345"


def test_to_pdf_url_passes_through_other():
    """Non-arxiv URLs are returned unchanged."""
    url = "https://example.com/paper.pdf"
    assert to_pdf_url(url) == url


async def test_extract_pdf_downloads_through_http_fetch_as_a_page():
    """PDF downloads go through http.fetch as pages: browser UA and the proxy fallback."""
    response = MagicMock()
    response.content = b"not a real pdf"
    settings = Settings(_env_file=None)

    with patch(
        "feed_brain.services.pdf.http.fetch", new_callable=AsyncMock, return_value=response
    ) as fetch:
        await extract_pdf("https://arxiv.org/abs/2401.12345", settings)

    fetch.assert_awaited_once_with("https://arxiv.org/pdf/2401.12345", settings, kind="page")
