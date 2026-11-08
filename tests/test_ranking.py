# ABOUTME: Tests for the deterministic story ranking used by the daily bulletin.
# ABOUTME: Score = tier weight + multi-source boost + confidence + section weight; top selection.

import pytest

from feed_brain.services.ranking import (
    SECTION_WEIGHT,
    Candidate,
    Story,
    rank_stories,
    select_top,
    source_boost,
)


def _c(article_id, source="A", tier="medium", section="tech", confidence=0.5, content_len=100):
    return Candidate(
        article_id=article_id,
        title=f"T{article_id}",
        url=f"https://x/{article_id}",
        source=source,
        tier=tier,
        section=section,
        confidence=confidence,
        summary=f"S{article_id}. Second sentence.",
        content_len=content_len,
    )


def test_source_boost_steps():
    assert [source_boost(n) for n in (0, 1, 2, 3, 4, 9)] == [0.0, 0.0, 0.5, 0.8, 1.0, 1.0]


def test_story_score_uses_max_tier_distinct_sources_and_max_confidence():
    story = Story(
        section="tech",
        articles=[
            _c(1, source="Ars", tier="high", confidence=0.7),
            _c(2, source="Ars", tier="medium", confidence=0.9),
            _c(3, source="Register", tier="low", confidence=0.4),
        ],
        headline="h",
    )
    assert story.distinct_sources == 2
    assert story.score == 2.0 + 0.5 + 0.9


def test_lead_prefers_confidence_then_longer_content():
    story = Story(
        section="tech",
        articles=[
            _c(1, confidence=0.8, content_len=10),
            _c(2, confidence=0.8, content_len=50),
            _c(3, confidence=0.6),
        ],
        headline="h",
    )
    assert story.lead.article_id == 2


def test_rank_orders_by_score_with_multi_source_beating_single_high():
    single_high = Story("tech", [_c(1, tier="high", confidence=0.6)], "a")
    multi_medium = Story(
        "tech",
        [
            _c(2, source="A", confidence=0.9),
            _c(3, source="B"),
            _c(4, source="C"),
            _c(5, source="D"),
        ],
        "b",
    )
    low = Story("sport", [_c(6, tier="low")], "c")
    assert [s.headline for s in rank_stories([low, single_high, multi_medium])] == ["b", "a", "c"]
    # 2.0+0+0.6 = 2.6 vs 1.0+1.0+0.9 = 2.9: multi-source medium wins
    assert rank_stories([single_high, multi_medium])[0].headline == "b"


def test_select_top_caps_and_keeps_the_rest_ranked():
    stories = [Story("tech", [_c(i, confidence=i / 100)], f"s{i}") for i in range(2, 8)]
    top, rest = select_top(stories, 3)
    assert [s.headline for s in top] == ["s7", "s6", "s5"]
    assert [s.headline for s in rest] == ["s4", "s3", "s2"]


def test_section_weight_favours_politics_and_economy_over_security():
    assert SECTION_WEIGHT == {"politics": 0.5, "economy": 0.5, "security": -0.5}
    same = [_c(1, tier="high", confidence=0.9)]
    scores = {section: Story(section, same, "h").score for section in SECTION_WEIGHT}
    assert scores == pytest.approx({"politics": 3.4, "economy": 3.4, "security": 2.4})
    # Sections without a weight, and every thematic edition's sections, keep the plain score.
    assert Story("ai", same, "h").score == pytest.approx(2.9)
    assert Story("threat_radar", same, "h").score == pytest.approx(2.9)


def test_single_source_politics_beats_two_source_security_but_not_a_major_breach():
    politics = Story("politics", [_c(1, tier="high", confidence=0.9)], "politics")  # 3.4
    security_two = Story(
        "security",
        [_c(2, source="A", tier="high", confidence=0.9), _c(3, source="B", tier="high")],
        "security_two",
    )  # 2.0 + 0.5 + 0.9 - 0.5 = 2.9
    major_breach = Story(
        "security",
        [_c(i, source=f"S{i}", tier="high", confidence=0.95) for i in range(4, 9)],
        "major_breach",
    )  # 2.0 + 1.0 + 0.95 - 0.5 = 3.45
    ranked = rank_stories([security_two, politics, major_breach])
    assert [s.headline for s in ranked] == ["major_breach", "politics", "security_two"]
