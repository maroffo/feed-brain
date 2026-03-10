# ABOUTME: Tests for daily digest generation.
# ABOUTME: Verifies formatting, priority sorting, frontmatter, translation, and collapsible low-tier section.

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from feed_brain.db.models import Article


@pytest.fixture
def make_article():
    """Factory for creating Article instances with classified_at set to now."""
    now = datetime.now(UTC)

    def _make(
        title="Test Article",
        url="https://example.com/test",
        tier="high",
        category="ai_agents",
        confidence=0.9,
        status="triaged",
        summary="A test summary.",
        reason="Relevant to interests.",
        deep_summary=None,
        deep_insights=None,
        money_quote=None,
        classified_at=None,
    ):
        return Article(
            title=title,
            url=url,
            tier=tier,
            category=category,
            confidence=confidence,
            status=status,
            summary=summary,
            reason=reason,
            deep_summary=deep_summary,
            deep_insights=deep_insights,
            money_quote=money_quote,
            classified_at=classified_at or now,
        )

    return _make


def _make_ollama_response(text):
    """Create a mock Ollama chat response."""
    response = MagicMock()
    response.message = MagicMock()
    response.message.content = text
    return response


@pytest.fixture
def mock_ollama_echo():
    """Mock Ollama client that echoes back input JSON (no-op translation)."""

    async def _echo_chat(**kwargs):
        messages = kwargs.get("messages", [])
        user_content = messages[-1]["content"] if messages else "{}"
        return _make_ollama_response(user_content)

    client = AsyncMock()
    client.chat = AsyncMock(side_effect=_echo_chat)
    return client


@pytest.fixture
def mock_ollama_translate():
    """Mock Ollama client that returns Italian translations."""

    async def _translate_chat(**kwargs):
        messages = kwargs.get("messages", [])
        user_content = messages[-1]["content"] if messages else "{}"
        fields = json.loads(user_content)
        translated = {}
        for key, value in fields.items():
            if isinstance(value, list):
                translated[key] = [f"[IT] {v}" for v in value]
            else:
                translated[key] = f"[IT] {value}"
        return _make_ollama_response(json.dumps(translated, ensure_ascii=False))

    client = AsyncMock()
    client.chat = AsyncMock(side_effect=_translate_chat)
    return client


class TestGenerateDigestFile:
    """Tests for digest file creation and path."""

    async def test_creates_correct_path(self, db_session, make_article, tmp_path, mock_ollama_echo):
        """Digest file created at Digests/Selezione - YYYY-MM-DD.md."""
        from feed_brain.services.digest import generate_digest

        article = make_article()
        db_session.add(article)
        await db_session.commit()

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        count = await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )

        assert count == 1
        expected = tmp_path / "Digests" / f"Selezione - {today}.md"
        assert expected.exists()

    async def test_empty_day_no_file(self, db_session, tmp_path, mock_ollama_echo):
        """No file created when no articles classified today."""
        from feed_brain.services.digest import generate_digest

        count = await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )

        assert count == 0
        digests_dir = tmp_path / "Digests"
        if digests_dir.exists():
            assert list(digests_dir.iterdir()) == []

    async def test_excludes_new_and_error_status(
        self, db_session, make_article, tmp_path, mock_ollama_echo
    ):
        """Articles with status 'new' or 'error' are excluded."""
        from feed_brain.services.digest import generate_digest

        db_session.add(make_article(status="new", url="https://example.com/new"))
        db_session.add(make_article(status="error", url="https://example.com/error"))
        await db_session.commit()

        count = await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )
        assert count == 0


class TestDigestFrontmatter:
    """Tests for YAML frontmatter content."""

    async def test_has_correct_fields(self, db_session, make_article, tmp_path, mock_ollama_echo):
        """Frontmatter has date, type, tier counts, and total."""
        from feed_brain.services.digest import generate_digest

        db_session.add(make_article(tier="high", url="https://example.com/h1"))
        db_session.add(make_article(tier="high", url="https://example.com/h2"))
        db_session.add(make_article(tier="medium", url="https://example.com/m1"))
        db_session.add(make_article(tier="low", url="https://example.com/l1"))
        await db_session.commit()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        content = digest_file.read_text()

        assert content.startswith("---\n")
        assert "type: daily-digest" in content
        assert f"date: {today}" in content
        assert "high_count: 2" in content
        assert "medium_count: 1" in content
        assert "low_count: 1" in content
        assert "total: 4" in content


class TestDigestSorting:
    """Tests for priority-based sorting."""

    async def test_sorts_by_priority(self, db_session, make_article, tmp_path, mock_ollama_echo):
        """High articles appear before medium, sorted by confidence within tier."""
        from feed_brain.services.digest import generate_digest

        # Add in reverse order to verify sorting
        db_session.add(
            make_article(
                title="Medium Article",
                tier="medium",
                confidence=0.7,
                url="https://example.com/med",
            )
        )
        db_session.add(
            make_article(
                title="High Low Conf",
                tier="high",
                confidence=0.8,
                url="https://example.com/h-low",
            )
        )
        db_session.add(
            make_article(
                title="High High Conf",
                tier="high",
                confidence=0.95,
                url="https://example.com/h-high",
            )
        )
        await db_session.commit()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        content = digest_file.read_text()

        # High High Conf should come first, then High Low Conf, then Medium
        pos_hh = content.index("High High Conf")
        pos_hl = content.index("High Low Conf")
        pos_m = content.index("Medium Article")
        assert pos_hh < pos_hl < pos_m


class TestDigestDeepAnalysis:
    """Tests for deep analysis rendering."""

    async def test_includes_deep_analysis_for_high_tier(
        self, db_session, make_article, tmp_path, mock_ollama_echo
    ):
        """Money quote and insights appear for analyzed high-tier articles."""
        from feed_brain.services.digest import generate_digest

        db_session.add(
            make_article(
                title="Analyzed Article",
                tier="high",
                status="analyzed",
                confidence=0.95,
                deep_summary="A detailed deep summary of the article.",
                money_quote="The best code is no code at all.",
                deep_insights=json.dumps(["Insight one", "Insight two", "Insight three"]),
                url="https://example.com/analyzed",
            )
        )
        await db_session.commit()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        content = digest_file.read_text()

        assert "The best code is no code at all." in content
        assert "- Insight one" in content
        assert "- Insight two" in content
        # Only top 2 insights in digest
        assert "Insight three" not in content

    async def test_uses_deep_summary_over_triage_summary(
        self, db_session, make_article, tmp_path, mock_ollama_echo
    ):
        """Prefers deep_summary when available."""
        from feed_brain.services.digest import generate_digest

        db_session.add(
            make_article(
                title="Deep vs Triage",
                summary="Triage summary only.",
                deep_summary="Deep analysis summary instead.",
                url="https://example.com/deep-vs-triage",
            )
        )
        await db_session.commit()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        content = digest_file.read_text()

        assert "Deep analysis summary instead." in content
        assert "Triage summary only." not in content


class TestDigestLowTier:
    """Tests for collapsible low-tier section."""

    async def test_low_tier_in_collapsible(
        self, db_session, make_article, tmp_path, mock_ollama_echo
    ):
        """Low-tier articles rendered in Obsidian callout section."""
        from feed_brain.services.digest import generate_digest

        db_session.add(
            make_article(title="Important Article", tier="high", url="https://example.com/high")
        )
        db_session.add(
            make_article(
                title="Low Priority One",
                tier="low",
                confidence=0.5,
                url="https://example.com/low1",
            )
        )
        db_session.add(
            make_article(
                title="Low Priority Two",
                tier="low",
                confidence=0.4,
                url="https://example.com/low2",
            )
        )
        await db_session.commit()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        content = digest_file.read_text()

        assert "> [!info]- Filtrati (2 articoli)" in content
        assert "Low Priority One" in content
        assert "Low Priority Two" in content

    async def test_low_tier_only_still_creates_file(
        self, db_session, make_article, tmp_path, mock_ollama_echo
    ):
        """File is created even if only low-tier articles exist."""
        from feed_brain.services.digest import generate_digest

        db_session.add(
            make_article(
                title="Only Low",
                tier="low",
                confidence=0.3,
                url="https://example.com/only-low",
            )
        )
        await db_session.commit()

        count = await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )

        assert count == 1
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        assert digest_file.exists()


class TestDigestIdempotent:
    """Tests for overwrite/regeneration behavior."""

    async def test_overwrites_existing(self, db_session, make_article, tmp_path, mock_ollama_echo):
        """Running twice produces same content (idempotent)."""
        from feed_brain.services.digest import generate_digest

        db_session.add(
            make_article(title="Consistent Article", url="https://example.com/consistent")
        )
        await db_session.commit()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        content_first = digest_file.read_text()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_echo
        )
        content_second = digest_file.read_text()

        assert content_first == content_second


class TestDigestTranslation:
    """Tests for Italian translation of digest content."""

    async def test_translates_summary_and_reason(
        self, db_session, make_article, tmp_path, mock_ollama_translate
    ):
        """Summary and reason are translated to Italian in the digest."""
        from feed_brain.services.digest import generate_digest

        db_session.add(
            make_article(
                title="English Article",
                summary="This is an English summary.",
                reason="Relevant to AI tooling.",
                url="https://example.com/en",
            )
        )
        await db_session.commit()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_translate
        )

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        content = digest_file.read_text()

        assert "[IT] This is an English summary." in content
        assert "[IT] Relevant to AI tooling." in content

    async def test_translates_money_quote_and_insights(
        self, db_session, make_article, tmp_path, mock_ollama_translate
    ):
        """Money quote and insights are translated for high-tier articles."""
        from feed_brain.services.digest import generate_digest

        db_session.add(
            make_article(
                title="Deep Article",
                tier="high",
                status="analyzed",
                confidence=0.95,
                deep_summary="Deep analysis of transformers.",
                money_quote="Attention is all you need.",
                deep_insights=json.dumps(["First insight", "Second insight"]),
                url="https://example.com/deep",
            )
        )
        await db_session.commit()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_translate
        )

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        content = digest_file.read_text()

        assert "[IT] Attention is all you need." in content
        assert "[IT] First insight" in content
        assert "[IT] Second insight" in content

    async def test_low_tier_not_translated(
        self, db_session, make_article, tmp_path, mock_ollama_translate
    ):
        """Low-tier articles are not sent for translation (only title+link)."""
        from feed_brain.services.digest import generate_digest

        db_session.add(
            make_article(
                title="Low Article",
                tier="low",
                summary="Should not be translated.",
                url="https://example.com/low",
            )
        )
        await db_session.commit()

        await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=mock_ollama_translate
        )

        # Ollama should not have been called (no high/medium articles)
        mock_ollama_translate.chat.assert_not_called()

    async def test_translation_failure_falls_back_to_original(
        self, db_session, make_article, tmp_path
    ):
        """If translation fails, original English text is used."""
        from feed_brain.services.digest import generate_digest

        failing_client = AsyncMock()
        failing_client.chat = AsyncMock(side_effect=Exception("Ollama down"))

        db_session.add(
            make_article(
                title="Fallback Article",
                summary="Original English text.",
                reason="Original reason.",
                url="https://example.com/fallback",
            )
        )
        await db_session.commit()

        count = await generate_digest(
            session=db_session, vault_path=tmp_path, ollama_client=failing_client
        )

        assert count == 1
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        digest_file = tmp_path / "Digests" / f"Selezione - {today}.md"
        content = digest_file.read_text()

        # Falls back to original text
        assert "Original English text." in content
        assert "Original reason." in content
