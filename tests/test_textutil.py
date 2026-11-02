# ABOUTME: Tests for text hygiene helpers.
# ABOUTME: NUL bytes from Gemini crashed a real Postgres UPDATE; these guard the fix.

from feed_brain.textutil import clean_text


def test_clean_text_strips_nul_and_control_chars():
    assert clean_text("HackerBooks \x08\x00\x08 è un tool") == "HackerBooks  è un tool"


def test_clean_text_keeps_whitespace_and_none():
    assert clean_text("a\tb\nc\r\n") == "a\tb\nc\r\n"
    assert clean_text(None) is None
