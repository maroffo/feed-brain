# ABOUTME: Tests for the Gemini structured-output client.
# ABOUTME: Covers parsing, retry on transient API errors and bad JSON, and final failure.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import errors
from pydantic import BaseModel
from tenacity import wait_none

from feed_brain.ai import gemini as gemini_mod
from feed_brain.ai.gemini import Gemini, GeminiError


class _Out(BaseModel):
    label: str
    score: float


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(gemini_mod, "_WAIT", wait_none())


def _client(*results) -> MagicMock:
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(side_effect=list(results))
    return client


def _resp(text: str | None) -> SimpleNamespace:
    return SimpleNamespace(text=text, candidates=[])


async def test_generate_parses_schema():
    client = _client(_resp('{"label": "x", "score": 0.5}'))
    out = await Gemini(client=client, model="m").generate("p", "sys", _Out)
    assert out == _Out(label="x", score=0.5)
    kwargs = client.aio.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "m"
    assert kwargs["config"].response_json_schema == _Out.model_json_schema()
    assert kwargs["config"].response_mime_type == "application/json"


async def test_generate_retries_transient_api_error():
    client = _client(
        errors.APIError(429, {"error": {"message": "rate"}}),
        _resp('{"label": "ok", "score": 1}'),
    )
    out = await Gemini(client=client, model="m").generate("p", "sys", _Out)
    assert out.label == "ok"
    assert client.aio.models.generate_content.await_count == 2


async def test_generate_retries_invalid_json_then_fails():
    client = _client(*[_resp("not json")] * 4)
    with pytest.raises(GeminiError):
        await Gemini(client=client, model="m").generate("p", "sys", _Out)
    assert client.aio.models.generate_content.await_count == 4


async def test_generate_does_not_retry_client_errors():
    client = _client(errors.APIError(400, {"error": {"message": "bad request"}}))
    with pytest.raises(GeminiError):
        await Gemini(client=client, model="m").generate("p", "sys", _Out)
    assert client.aio.models.generate_content.await_count == 1


async def test_generate_text_returns_plain_text():
    client = _client(_resp("  an answer  "))
    out = await Gemini(client=client, model="m").generate_text("q", "sys")
    assert out == "an answer"
    assert (
        client.aio.models.generate_content.call_args.kwargs["config"].response_json_schema is None
    )


async def test_generate_text_empty_is_an_error():
    client = _client(*[_resp(None)] * 4)
    with pytest.raises(GeminiError):
        await Gemini(client=client, model="m").generate_text("q", "sys")


async def test_generate_retries_transport_errors_and_wraps_them():
    """httpx transport failures (not APIError) are retried, then surface as GeminiError."""
    import httpx

    client = _client(
        httpx.ConnectTimeout("timeout"),
        _resp('{"label": "ok", "score": 1}'),
    )
    assert (await Gemini(client=client, model="m").generate("p", "sys", _Out)).label == "ok"

    client = _client(*[httpx.ReadError("reset")] * 4)
    with pytest.raises(GeminiError):
        await Gemini(client=client, model="m").generate("p", "sys", _Out)


def test_missing_api_key_raises():
    from feed_brain.config import Settings

    with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
        Gemini(settings=Settings(_env_file=None, gemini_api_key=None))
