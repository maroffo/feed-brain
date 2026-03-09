# ABOUTME: Tests for the Ollama triage service.
# ABOUTME: Verifies triage parsing, retry logic, and error handling.

import json
from unittest.mock import AsyncMock, MagicMock

from feed_brain.db.models import Article
from feed_brain.models import Category, Tier
from feed_brain.services.triage import TriageParseError, triage_article


def _mock_ollama_response(data: dict) -> MagicMock:
    """Create a mock Ollama chat response."""
    response = MagicMock()
    response.message = MagicMock()
    response.message.content = json.dumps(data)
    return response


async def test_triage_article_success():
    """Valid Ollama response is parsed into TriageResult."""
    article = Article(
        url="https://example.com/ai-agents",
        title="Building AI Agents with Context Engineering",
        content="This article discusses advanced context engineering patterns...",
        status="new",
    )

    api_data = {
        "tier": "high",
        "category": "ai_agents",
        "confidence": 0.92,
        "summary": "Overview of context engineering patterns for AI agents.",
        "reason": "Core topic of interest with practical patterns.",
    }

    client = AsyncMock()
    client.chat = AsyncMock(return_value=_mock_ollama_response(api_data))

    result = await triage_article(article, client=client)

    assert result is not None
    assert result.tier == Tier.HIGH
    assert result.category == Category.AI_AGENTS
    assert result.confidence == 0.92
    assert "context engineering" in result.summary.lower()


async def test_triage_article_invalid_json_raises_parse_error():
    """Invalid JSON triggers TriageParseError (for retry)."""
    article = Article(url="https://example.com/bad", title="Bad", content="content", status="new")

    response = MagicMock()
    response.message = MagicMock()
    response.message.content = "This is not JSON"

    client = AsyncMock()
    client.chat = AsyncMock(return_value=response)

    import pytest

    with pytest.raises(TriageParseError):
        await triage_article(article, client=client)


async def test_triage_article_connection_error_returns_none():
    """Connection errors return None without raising."""
    article = Article(url="https://example.com/err", title="Err", content="content", status="new")

    client = AsyncMock()
    client.chat = AsyncMock(side_effect=ConnectionError("Ollama not running"))

    result = await triage_article(article, client=client)
    assert result is None


async def test_triage_article_missing_key_raises_parse_error():
    """Response missing required keys triggers TriageParseError."""
    article = Article(
        url="https://example.com/missing", title="Missing", content="content", status="new"
    )

    api_data = {"tier": "high", "category": "ai_agents"}  # missing confidence, summary, reason

    client = AsyncMock()
    client.chat = AsyncMock(return_value=_mock_ollama_response(api_data))

    import pytest

    with pytest.raises(TriageParseError):
        await triage_article(article, client=client)


async def test_triage_article_low_tier():
    """Low-tier articles are correctly classified."""
    article = Article(
        url="https://example.com/marketing",
        title="10 Marketing Tips for 2026",
        content="Basic marketing strategies for beginners...",
        status="new",
    )

    api_data = {
        "tier": "low",
        "category": "marketing",
        "confidence": 0.85,
        "summary": "Generic marketing tips without novel frameworks.",
        "reason": "Basic tutorial content with no actionable insight.",
    }

    client = AsyncMock()
    client.chat = AsyncMock(return_value=_mock_ollama_response(api_data))

    result = await triage_article(article, client=client)

    assert result is not None
    assert result.tier == Tier.LOW
    assert result.category == Category.MARKETING
