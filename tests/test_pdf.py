# ABOUTME: Tests for PDF extraction and arxiv URL handling.
# ABOUTME: Verifies URL detection, conversion, and extraction logic.

from feed_brain.services.pdf import is_pdf_url, to_pdf_url


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
