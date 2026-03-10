# ABOUTME: Tests for the Anthropic deep analysis service.
# ABOUTME: Verifies analysis parsing, error handling, output structure, and Minions protocol.

import json
from unittest.mock import AsyncMock, MagicMock, patch

from feed_brain.db.models import Article
from feed_brain.services.analyzer import (
    _decompose,
    _extract_answer,
    _is_arxiv,
    _synthesize,
    analyze_article,
    analyze_article_minions,
)


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

    mock_settings = MagicMock()
    mock_settings.anthropic_api_key = None

    with patch("feed_brain.services.analyzer.get_settings", return_value=mock_settings):
        result = await analyze_article(article, client=None)
    assert result is None


# --- Minions protocol tests ---


def _mock_ollama_response(text: str) -> MagicMock:
    """Create a mock Ollama chat response."""
    response = MagicMock()
    response.message = MagicMock()
    response.message.content = text
    return response


async def test_is_arxiv_detects_abs_url():
    """arXiv /abs/ URL is detected."""
    article = Article(
        url="https://arxiv.org/abs/2401.12345",
        title="Paper",
        content="content",
        status="triaged",
        tier="high",
    )
    assert _is_arxiv(article) is True


async def test_is_arxiv_detects_pdf_url():
    """arXiv /pdf/ URL is detected."""
    article = Article(
        url="https://arxiv.org/pdf/2401.12345",
        title="Paper",
        content="content",
        status="triaged",
        tier="high",
    )
    assert _is_arxiv(article) is True


async def test_is_arxiv_rejects_regular_url():
    """Non-arXiv URL is not detected as arXiv."""
    article = Article(
        url="https://example.com/article",
        title="Article",
        content="content",
        status="triaged",
        tier="high",
    )
    assert _is_arxiv(article) is False


async def test_decompose_returns_questions():
    """Sonnet response with JSON array of questions is parsed correctly."""
    questions = [
        "What is the main contribution?",
        "What methodology is used?",
        "What are the key results?",
    ]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_mock_anthropic_response(questions))

    result = await _decompose("Paper Title", "Abstract text", client, "claude-sonnet-4-6")

    assert result == questions
    assert len(result) == 3


async def test_decompose_caps_at_five():
    """If Sonnet returns more than 5 questions, only 5 are used."""
    questions = [f"Question {i}?" for i in range(7)]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_mock_anthropic_response(questions))

    result = await _decompose("Paper Title", "Abstract text", client, "claude-sonnet-4-6")

    assert len(result) == 5


async def test_extract_answer_calls_ollama():
    """Ollama response text is returned as the answer."""
    ollama_client = AsyncMock()
    ollama_client.chat = AsyncMock(
        return_value=_mock_ollama_response("The main contribution is X.")
    )

    result = await _extract_answer(
        "What is the main contribution?",
        "Full paper content here...",
        ollama_client,
        "qwen3.5:9b",
    )

    assert result == "The main contribution is X."
    ollama_client.chat.assert_called_once()


async def test_synthesize_returns_deep_analysis():
    """Sonnet synthesizes Q&A pairs into DeepAnalysis."""
    analysis_data = {
        "summary": "This paper presents a novel approach to X.",
        "insights": ["Key insight about methodology", "Surprising finding"],
        "money_quote": "Our results demonstrate a 40% improvement.",
        "actionables": ["Apply technique X to Y", "Consider approach Z"],
    }
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_mock_anthropic_response(analysis_data))

    qa_pairs = [
        ("What is the main contribution?", "The paper proposes X."),
        ("What are the results?", "40% improvement over baseline."),
    ]

    result = await _synthesize("Paper Title", qa_pairs, client, "claude-sonnet-4-6")

    assert result is not None
    assert "novel approach" in result.summary
    assert len(result.insights) == 2
    assert result.money_quote == "Our results demonstrate a 40% improvement."
    assert len(result.actionables) == 2


async def test_analyze_article_minions_full_flow():
    """Full Minions flow: decompose, extract answers, synthesize into DeepAnalysis."""
    article = Article(
        url="https://arxiv.org/abs/2401.99999",
        title="Attention Is All You Need (Again)",
        content="A" * 10000,  # Long paper content
        status="triaged",
        tier="high",
    )

    questions = ["What is the contribution?", "What are the results?"]
    analysis_data = {
        "summary": "Novel transformer variant achieves state-of-the-art.",
        "insights": ["Efficiency improvement"],
        "money_quote": "We achieve 95% accuracy.",
        "actionables": ["Try the new attention mechanism"],
    }

    anthropic_client = AsyncMock()
    # First call: decompose (returns questions), second call: synthesize (returns analysis)
    anthropic_client.messages.create = AsyncMock(
        side_effect=[
            _mock_anthropic_response(questions),
            _mock_anthropic_response(analysis_data),
        ]
    )

    ollama_client = AsyncMock()
    ollama_client.chat = AsyncMock(
        side_effect=[
            _mock_ollama_response("The contribution is a new transformer."),
            _mock_ollama_response("Results show 95% accuracy."),
        ]
    )

    result = await analyze_article_minions(
        article,
        anthropic_client=anthropic_client,
        ollama_client=ollama_client,
    )

    assert result is not None
    assert "transformer" in result.summary.lower()
    # decompose + synthesize = 2 Anthropic calls
    assert anthropic_client.messages.create.call_count == 2
    # 2 questions = 2 Ollama calls
    assert ollama_client.chat.call_count == 2


async def test_analyze_article_minions_fallback_on_decompose_failure():
    """If decompose fails, analyze_article_minions returns None."""
    article = Article(
        url="https://arxiv.org/abs/2401.99999",
        title="Paper",
        content="Content",
        status="triaged",
        tier="high",
    )

    anthropic_client = AsyncMock()
    anthropic_client.messages.create = AsyncMock(side_effect=Exception("API error"))

    ollama_client = AsyncMock()

    result = await analyze_article_minions(
        article,
        anthropic_client=anthropic_client,
        ollama_client=ollama_client,
    )

    assert result is None


async def test_analyze_article_minions_fallback_on_extract_failure():
    """If Ollama extraction fails, analyze_article_minions returns None."""
    article = Article(
        url="https://arxiv.org/abs/2401.99999",
        title="Paper",
        content="Content",
        status="triaged",
        tier="high",
    )

    questions = ["What is the contribution?"]

    anthropic_client = AsyncMock()
    anthropic_client.messages.create = AsyncMock(return_value=_mock_anthropic_response(questions))

    ollama_client = AsyncMock()
    ollama_client.chat = AsyncMock(side_effect=Exception("Ollama timeout"))

    result = await analyze_article_minions(
        article,
        anthropic_client=anthropic_client,
        ollama_client=ollama_client,
    )

    assert result is None
