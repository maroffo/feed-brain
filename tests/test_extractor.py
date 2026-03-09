# ABOUTME: Tests for content extraction with PDF routing and trafilatura fallback.
# ABOUTME: Verifies readability extraction, fallback chain, and error handling.

from unittest.mock import AsyncMock, MagicMock, patch

from feed_brain.services.extractor import _sanitize_html, extract_content


def test_sanitize_html_strips_scripts():
    """Script and style tags are unwrapped."""
    long_text = "This is a paragraph with enough text to pass the minimum length check. " * 2
    html = f"<div><p>{long_text}</p><script>alert(1)</script></div>"
    result = _sanitize_html(html)
    assert result is not None
    assert "<script>" not in result
    assert long_text[:20] in result


def test_sanitize_html_preserves_allowed_tags():
    """Allowed tags like p, a, strong are preserved."""
    long_text = "This is a paragraph with enough text to pass the minimum length requirement. "
    html = f'<p>{long_text}<strong>world</strong> <a href="http://x.com">link</a></p>'
    result = _sanitize_html(html)
    assert result is not None
    assert "<strong>" in result
    assert '<a href="http://x.com">' in result


def test_sanitize_html_returns_none_for_short_text():
    """Returns None if extracted text is too short."""
    html = "<p>Hi</p>"
    result = _sanitize_html(html)
    assert result is None


async def test_extract_content_routes_pdf():
    """PDF URLs are routed to pdf extractor."""
    with patch(
        "feed_brain.services.extractor.extract_pdf",
        new_callable=AsyncMock,
        return_value="PDF text content here",
    ) as mock_pdf:
        result = await extract_content("https://arxiv.org/abs/2401.12345")
        assert result == "PDF text content here"
        mock_pdf.assert_called_once()


async def test_extract_content_readability_success():
    """Readability extraction returns sanitized HTML."""
    html = "<html><body><article>" + "<p>Content paragraph.</p>" * 10 + "</article></body></html>"

    mock_response = MagicMock()
    mock_response.text = html
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("feed_brain.services.extractor.httpx.AsyncClient", return_value=mock_client):
        result = await extract_content("https://example.com/article")

    assert result is not None
    assert "Content paragraph" in result
