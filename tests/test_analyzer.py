# ABOUTME: Tests for the Anthropic deep analysis service.
# ABOUTME: Verifies analysis parsing, error handling, and output structure.

import json
from unittest.mock import AsyncMock, MagicMock

from feed_brain.db.models import Article
from feed_brain.services.analyzer import analyze_article


def _mock_anthropic_response(data: dict) -> MagicMock:
    """Create a mock Anthropic API response."""
    content_block = MagicMock()
    content_block.text = json.dumps(data)
    response = MagicMock()
    response.content = [content_block]
    return response


async def test_analyze_article_success():
    """Valid API response is parsed into DeepAnalysis."""
    article = Article(
        url="https://example.com/deep",
        title="Deep Dive into Context Engineering",
        content="This article provides a comprehensive analysis...",
        status="triaged",
        tier="high",
    )

    api_data = {
        "summary": "Comprehensive analysis of context engineering patterns.",
        "insights": ["Insight one", "Insight two"],
        "money_quote": "The best code is the code you don't write.",
        "actionables": ["Try pattern X", "Use framework Y"],
    }

    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_mock_anthropic_response(api_data))

    result = await analyze_article(article, client=client)

    assert result is not None
    assert "context engineering" in result.summary.lower()
    assert len(result.insights) == 2
    assert result.money_quote == "The best code is the code you don't write."
    assert len(result.actionables) == 2


async def test_analyze_article_invalid_json():
    """Invalid JSON response returns None."""
    article = Article(
        url="https://example.com/bad", title="Bad", content="content", status="triaged", tier="high"
    )

    content_block = MagicMock()
    content_block.text = "Not valid JSON"
    response = MagicMock()
    response.content = [content_block]

    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    result = await analyze_article(article, client=client)
    assert result is None


async def test_analyze_article_api_error():
    """API errors return None without crashing."""
    article = Article(
        url="https://example.com/err", title="Err", content="content", status="triaged", tier="high"
    )

    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=Exception("API timeout"))

    result = await analyze_article(article, client=client)
    assert result is None


async def test_analyze_article_no_api_key():
    """Missing API key returns None."""
    article = Article(
        url="https://example.com/nokey",
        title="No Key",
        content="content",
        status="triaged",
        tier="high",
    )

    result = await analyze_article(article, client=None)
    assert result is None
