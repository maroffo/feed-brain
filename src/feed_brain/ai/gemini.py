# ABOUTME: Async Gemini client: structured JSON output validated by Pydantic, plain text, retries.
# ABOUTME: Retries 429/5xx and unparseable responses with exponential backoff; safety filters off.

from collections.abc import Callable
from typing import TypeVar

import httpx
import structlog
from google import genai
from google.genai import errors, types
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from feed_brain.config import Settings, get_settings

log = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")

_ATTEMPTS = 4
_WAIT = wait_exponential(multiplier=2, min=2, max=30)
_TRANSIENT_CODES = {429, 500, 502, 503, 504}

# Security and news content trips the default filters; the reader wants it unfiltered.
_SAFETY_OFF = [
    types.SafetySetting(category=category, threshold=types.HarmBlockThreshold.OFF)
    for category in (
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
    )
]


class GeminiError(Exception):
    """Raised when Gemini fails permanently or keeps failing after retries."""


class _Retryable(Exception):
    """Transient failure worth another attempt."""


class Gemini:
    """Thin async wrapper around google-genai with structured output and retries."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: genai.Client | None = None,
        model: str | None = None,
    ) -> None:
        settings = settings or get_settings()
        if client is None:
            if settings.gemini_api_key is None:
                raise GeminiError("GEMINI_API_KEY is not set")
            client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._client = client
        self._model = model or settings.gemini_model

    async def generate(self, prompt: str, system: str, schema: type[T]) -> T:
        """Return the model's answer parsed into `schema` (JSON schema enforced server-side)."""
        return await self._call(
            prompt, system, schema.model_json_schema(), schema.model_validate_json
        )

    async def generate_text(self, prompt: str, system: str) -> str:
        """Return a plain-text answer."""
        return await self._call(prompt, system, None, str)

    async def _call(
        self, prompt: str, system: str, json_schema: dict | None, parse: Callable[[str], R]
    ) -> R:
        config = types.GenerateContentConfig(
            system_instruction=system,
            safety_settings=_SAFETY_OFF,
            response_mime_type="application/json" if json_schema else "text/plain",
            response_json_schema=json_schema,
        )
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(_ATTEMPTS),
                wait=_WAIT,
                retry=retry_if_exception_type(_Retryable),
                before_sleep=lambda rs: log.warning(
                    "gemini_retry",
                    attempt=rs.attempt_number,
                    error=str(rs.outcome.exception()) if rs.outcome else None,
                ),
            ):
                with attempt:
                    text = await self._attempt(prompt, config)
                    try:
                        return parse(text)
                    except ValueError as e:  # includes pydantic.ValidationError
                        raise _Retryable(f"unparseable response: {e}") from e
        except RetryError as e:
            cause = e.last_attempt.exception()
            raise GeminiError(f"gave up after {_ATTEMPTS} attempts: {cause}") from e
        raise AssertionError("unreachable")  # pragma: no cover - AsyncRetrying returns or raises

    async def _attempt(self, prompt: str, config: types.GenerateContentConfig) -> str:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=prompt, config=config
            )
        except errors.APIError as e:
            if e.code in _TRANSIENT_CODES:
                raise _Retryable(f"{e.code} {e.status}") from e
            raise GeminiError(f"{e.code} {e.status}: {e.message}") from e
        except (httpx.HTTPError, TimeoutError, OSError) as e:
            # google-genai lets transport failures through unwrapped
            raise _Retryable(f"transport error: {e!r}") from e

        text = (response.text or "").strip()
        if not text:
            raise _Retryable("empty response")
        return text
