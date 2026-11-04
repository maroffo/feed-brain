# ABOUTME: Tests for content extraction with PDF routing and trafilatura fallback.
# ABOUTME: Verifies readability extraction, fallback chain, and error handling.

from unittest.mock import AsyncMock, MagicMock, patch

from feed_brain.config import Settings
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

    with patch("feed_brain.services.http.httpx.AsyncClient", return_value=mock_client):
        result = await extract_content("https://example.com/article")

    assert result is not None
    assert "Content paragraph" in result


async def test_extract_content_uses_the_page_user_agent():
    """Article pages are fetched with the browser UA (many sites reject bot UAs on HTML)."""
    html = "<html><body><article>" + "<p>Content paragraph.</p>" * 10 + "</article></body></html>"
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    settings = Settings(_env_file=None, feed_user_agent="feed-ua", page_user_agent="page-ua")

    with patch("feed_brain.services.http.httpx.AsyncClient", return_value=mock_client) as client:
        await extract_content("https://example.com/article", settings)

    assert client.call_args.kwargs["headers"] == {"User-Agent": "page-ua"}


def test_a_short_readability_result_loses_to_a_longer_trafilatura_one(monkeypatch):
    """Politico: readability returns the 226-char teaser, trafilatura the article."""
    from feed_brain.services import extractor

    monkeypatch.setattr(
        extractor, "_extract_with_readability", lambda _h: "<p>" + "teaser " * 30 + "</p>"
    )
    monkeypatch.setattr(extractor, "_extract_with_trafilatura", lambda _h: "full text " * 500)
    assert extractor.extract_from_html("<html/>").startswith("full text")


def test_a_long_readability_result_is_kept_without_running_trafilatura(monkeypatch):
    from feed_brain.services import extractor

    called = []
    monkeypatch.setattr(
        extractor, "_extract_with_readability", lambda _h: "<p>" + "parola " * 400 + "</p>"
    )
    monkeypatch.setattr(extractor, "_extract_with_trafilatura", lambda _h: called.append(1) or "x")
    assert "parola" in extractor.extract_from_html("<html/>") and called == []


def test_a_readability_crash_falls_back_to_trafilatura(monkeypatch):
    from feed_brain.services import extractor

    def crash(_html):
        raise ValueError("All strings must be XML compatible")

    monkeypatch.setattr(extractor, "_extract_with_readability", crash)
    monkeypatch.setattr(extractor, "_extract_with_trafilatura", lambda _h: "testo " * 100)
    assert extractor.extract_from_html("<html/>").startswith("testo")


async def test_extract_content_never_parses_an_image(monkeypatch):
    import pytest

    from feed_brain.services import extractor

    response = MagicMock()
    response.text = "\x89JFIF binary"
    response.headers = {"content-type": "image/jpeg"}
    monkeypatch.setattr(extractor.http, "fetch", AsyncMock(return_value=response))
    monkeypatch.setattr(
        extractor, "_extract_with_readability", lambda _h: pytest.fail("parsed an image")
    )
    url = "https://images.dday.it/x/crop.jpg"
    assert await extract_content(url, Settings(_env_file=None)) is None
