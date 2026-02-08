# ABOUTME: Tests for the Haiku classifier service.
# ABOUTME: Verifies classification parsing, error handling, and tier assignment.

import json
from unittest.mock import AsyncMock, MagicMock

from feed_brain.db.models import Article
from feed_brain.models import Category, Tier
from feed_brain.services.classifier import classify_article


def _mock_anthropic_response(data: dict) -> MagicMock:
    """Create a mock Anthropic API response."""
    content_block = MagicMock()
    content_block.text = json.dumps(data)
    response = MagicMock()
    response.content = [content_block]
    return response


async def test_classify_article_success():
    """Valid API response is parsed into ClassificationResult."""
    article = Article(
        url="https://example.com/ai-agents",
        title="Building AI Agents with Context Engineering",
        content="This article discusses advanced context engineering patterns...",
    )

    api_data = {
        "tier": "high",
        "category": "ai_agents",
        "summary": "Overview of context engineering patterns for AI agents.",
        "reason": "Core topic of interest with practical patterns.",
        "confidence": 0.92,
    }

    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_mock_anthropic_response(api_data))

    result = await classify_article(article, client=client)

    assert result is not None
    assert result.tier == Tier.HIGH
    assert result.category == Category.AI_AGENTS
    assert result.confidence == 0.92
    assert "context engineering" in result.summary.lower()


async def test_classify_article_invalid_json():
    """Invalid JSON response returns None."""
    article = Article(url="https://example.com/bad", title="Bad", content="content")

    content_block = MagicMock()
    content_block.text = "This is not JSON"
    response = MagicMock()
    response.content = [content_block]

    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    result = await classify_article(article, client=client)
    assert result is None


async def test_classify_article_api_error():
    """API errors return None without crashing."""
    article = Article(url="https://example.com/err", title="Err", content="content")

    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=Exception("API timeout"))

    result = await classify_article(article, client=client)
    assert result is None


async def test_classify_article_missing_key():
    """Response missing required keys returns None."""
    article = Article(url="https://example.com/missing", title="Missing", content="content")

    api_data = {"tier": "high", "category": "ai_agents"}  # missing summary, reason, confidence

    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_mock_anthropic_response(api_data))

    result = await classify_article(article, client=client)
    assert result is None
