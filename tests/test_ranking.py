# ABOUTME: Tests for the deterministic story ranking used by the daily bulletin.
# ABOUTME: Score = tier weight + multi-source boost + confidence; radar ordering and cap.

from feed_brain.services.ranking import (
    Candidate,
    Story,
    radar_items,
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
        section="security",
        articles=[
            _c(1, source="Krebs", tier="high", confidence=0.7),
            _c(2, source="Krebs", tier="medium", confidence=0.9),
            _c(3, source="Schneier", tier="low", confidence=0.4),
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
    single_high = Story("security", [_c(1, tier="high", confidence=0.6)], "a")
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


def test_select_top_excludes_threat_radar_and_caps():
    stories = [Story("threat_radar", [_c(1, tier="high", confidence=1.0)], "r")] + [
        Story("tech", [_c(i, confidence=i / 100)], f"s{i}") for i in range(2, 8)
    ]
    top, rest = select_top(stories, 3)
    assert [s.headline for s in top] == ["s7", "s6", "s5"]
    assert {s.headline for s in rest} == {"s2", "s3", "s4"}


def test_radar_items_sorted_by_tier_then_confidence_and_capped():
    items = [
        _c(1, tier="low", confidence=0.9, section="threat_radar"),
        _c(2, tier="medium", confidence=0.5, section="threat_radar"),
        _c(3, tier="high", confidence=0.1, section="threat_radar"),
        _c(4, tier="low", confidence=0.2, section="threat_radar"),
    ]
    assert [c.article_id for c in radar_items(items, cap=3)] == [3, 2, 1]
