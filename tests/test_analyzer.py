# ABOUTME: Tests for Opus 5.5 deep analysis and the Minions protocol (Opus decomposes, Gemini reads).
# ABOUTME: The Anthropic client and Gemini are mocked; request shape, refusals and fallbacks are asserted.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
from anthropic import APIConnectionError

from feed_brain.ai.gemini import GeminiError
from feed_brain.config import Settings
from feed_brain.db.models import Article
from feed_brain.models import ArticleStatus, DeepAnalysis
from feed_brain.services.analyzer import (
    FALLBACK_BETA,
    Questions,
    analyze_article,
    analyze_article_minions,
    apply_analysis,
    deep_analyze,
    is_arxiv,
)

ANALYSIS = DeepAnalysis(
    summary="Sintesi.", insights=["a", "b"], money_quote="Quote.", actionables=["Prova X"]
)
SETTINGS = Settings(_env_file=None, analyzer_model="claude-opus-5-5", analyzer_effort="high")


def _response(parsed, stop_reason="end_turn", stop_details=None, model="claude-opus-5-5"):
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        stop_details=stop_details,
        model=model,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=800),
    )


def _client(*responses) -> MagicMock:
    client = MagicMock()
    client.beta.messages.parse = AsyncMock(side_effect=list(responses))
    return client


def _article(url="https://example.com/a", content="Body " * 100) -> Article:
    return Article(url=url, title="Title", author="Author", content=content, status="triaged")


async def test_analyze_article_request_shape():
    client = _client(_response(ANALYSIS))

    result = await analyze_article(_article(), client, SETTINGS)

    assert result == ANALYSIS
    kwargs = client.beta.messages.parse.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_format"] is DeepAnalysis
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["fallbacks"] == "default"
    assert kwargs["betas"] == [FALLBACK_BETA] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in kwargs and "temperature" not in kwargs
    assert "Title: Title" in kwargs["messages"][0]["content"]
    assert "Italian" in kwargs["system"]


async def test_analyze_article_refusal_returns_none():
    client = _client(
        _response(None, stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    )
    assert await analyze_article(_article(), client, SETTINGS) is None


async def test_analyze_article_max_tokens_returns_none():
    client = _client(_response(None, stop_reason="max_tokens"))
    assert await analyze_article(_article(), client, SETTINGS) is None


async def test_analyze_article_api_error_returns_none():
    client = _client(APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")))
    assert await analyze_article(_article(), client, SETTINGS) is None


def test_is_arxiv():
    assert is_arxiv(_article(url="https://arxiv.org/abs/2601.00001"))
    assert is_arxiv(_article(url="https://arxiv.org/pdf/2601.00001"))
    assert not is_arxiv(_article(url="https://example.com/arxiv-like"))


class FakeGemini:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.questions: list[str] = []

    async def generate_text(self, prompt, system):  # noqa: ARG002 - protocol signature
        if self.fail:
            raise GeminiError("down")
        self.questions.append(prompt.split("\n", 1)[0])
        return "answer from paper"


async def test_minions_flow_decompose_extract_synthesize():
    client = _client(
        _response(Questions(questions=["Q1?", "Q2?", "Q3?", "Q4?", "Q5?", "Q6?"])),
        _response(ANALYSIS),
    )
    gemini = FakeGemini()

    result = await analyze_article_minions(
        _article(url="https://arxiv.org/abs/1"), client, gemini, SETTINGS
    )

    assert result == ANALYSIS
    assert gemini.questions == [
        "Question: Q1?",
        "Question: Q2?",
        "Question: Q3?",
        "Question: Q4?",
        "Question: Q5?",
    ]
    first, second = client.beta.messages.parse.call_args_list
    assert first.kwargs["output_format"] is Questions
    assert second.kwargs["output_format"] is DeepAnalysis
    assert "Q: Q1?\nA: answer from paper" in second.kwargs["messages"][0]["content"]


async def test_deep_analyze_falls_back_to_direct_when_minions_fails():
    client = _client(_response(Questions(questions=["Q1?"])), _response(ANALYSIS))
    result = await deep_analyze(
        _article(url="https://arxiv.org/abs/1"), client, FakeGemini(fail=True), SETTINGS
    )
    assert result == ANALYSIS
    assert client.beta.messages.parse.call_args.kwargs["output_format"] is DeepAnalysis


async def test_deep_analyze_non_arxiv_goes_direct():
    client = _client(_response(ANALYSIS))
    assert await deep_analyze(_article(), client, FakeGemini(), SETTINGS) == ANALYSIS
    assert client.beta.messages.parse.await_count == 1


def test_apply_analysis_sets_fields_and_status():
    article = _article()
    apply_analysis(article, ANALYSIS)
    assert article.deep_summary == "Sintesi."
    assert article.deep_insights == '["a", "b"]'
    assert article.money_quote == "Quote."
    assert article.actionables == '["Prova X"]'
    assert article.status == ArticleStatus.ANALYZED


async def test_analyze_article_truncated_json_returns_none():
    """parse() raises ValidationError on JSON cut at max_tokens; analysis returns None."""
    from pydantic import ValidationError

    try:
        DeepAnalysis.model_validate_json('{"summary": "trunc')
    except ValidationError as e:
        error = e
    client = _client(error)
    assert await analyze_article(_article(), client, SETTINGS) is None


async def test_minions_without_questions_returns_none():
    for decomposed in (_response(None, stop_reason="refusal"), _response(Questions(questions=[]))):
        client = _client(decomposed)
        gemini = FakeGemini()
        result = await analyze_article_minions(
            _article(url="https://arxiv.org/abs/1"), client, gemini, SETTINGS
        )
        assert result is None
        assert gemini.questions == []


async def test_deep_analyze_arxiv_without_gemini_goes_direct():
    client = _client(_response(ANALYSIS))
    assert (
        await deep_analyze(_article(url="https://arxiv.org/abs/1"), client, None, SETTINGS)
        == ANALYSIS
    )
    assert client.beta.messages.parse.call_args.kwargs["output_format"] is DeepAnalysis


def test_make_client_requires_key():
    from pydantic import SecretStr

    from feed_brain.services.analyzer import make_client

    assert make_client(Settings(_env_file=None, anthropic_api_key=None)) is None
    assert make_client(Settings(_env_file=None, anthropic_api_key=SecretStr("sk-test"))) is not None


async def test_analysis_output_is_cleaned_of_control_chars():
    dirty = DeepAnalysis(
        summary="a\x00b", insights=["c\x01"], money_quote="q\x00", actionables=["d\x7f"]
    )
    client = _client(_response(dirty))
    result = await analyze_article(_article(), client, SETTINGS)
    assert result == DeepAnalysis(summary="ab", insights=["c"], money_quote="q", actionables=["d"])


async def test_analyzed_article_is_delimited_untrusted_data():
    client = _client(_response(ANALYSIS))
    await analyze_article(_article(), client, SETTINGS)
    kwargs = client.beta.messages.parse.call_args.kwargs
    content = kwargs["messages"][0]["content"]
    assert content.startswith("<article>\n") and content.endswith("\n</article>")
    assert "untrusted" in kwargs["system"]
