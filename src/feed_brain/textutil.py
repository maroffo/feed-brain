# ABOUTME: Text hygiene shared by fetcher and LLM outputs before they reach Postgres.
# ABOUTME: Postgres TEXT rejects NUL bytes; other C0 control characters are noise in a bulletin.

import re
from typing import overload

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@overload
def clean_text(value: str) -> str: ...
@overload
def clean_text(value: None) -> None: ...
def clean_text(value: str | None) -> str | None:
    """Drop NUL and other control characters (keeps tab, newline, carriage return)."""
    if value is None:
        return None
    return _CONTROL_CHARS.sub("", value)
