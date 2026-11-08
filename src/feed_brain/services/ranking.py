# ABOUTME: Deterministic ranking of bulletin stories: tier, multi-source boost, confidence, section.
# ABOUTME: Pure functions and dataclasses, no I/O; the plan's single source of truth for "importance".

from dataclasses import dataclass

TIER_WEIGHT = {"high": 2.0, "medium": 1.0, "low": 0.0}
# Editorial balance of the main issue: politics (major news events included) and economy
# rise, security sinks so that only its biggest multi-source stories still lead. Unlisted
# sections weigh 0, so the thematic editions keep the plain score. Only top stories move:
# briefs are listed per section, where a shared weight never changes the order.
SECTION_WEIGHT = {"politics": 0.5, "economy": 0.5, "security": -0.5}


def source_boost(distinct_sources: int) -> float:
    """Bonus for coverage by several feeds: 1 → 0, 2 → 0.5, 3 → 0.8, 4+ → 1.0."""
    if distinct_sources <= 1:
        return 0.0
    return {2: 0.5, 3: 0.8}.get(distinct_sources, 1.0)


@dataclass(frozen=True)
class Candidate:
    """A triaged article eligible for today's bulletin (plain data, no ORM)."""

    article_id: int
    title: str
    url: str
    source: str
    tier: str
    section: str
    confidence: float
    summary: str
    content_len: int
    paid: bool = False  # from the newsletter inbox


@dataclass
class Story:
    """One event, possibly covered by several articles, inside a section."""

    section: str
    articles: list[Candidate]
    headline: str

    @property
    def lead(self) -> Candidate:
        """Article that represents the story: highest confidence, then longest content."""
        return max(self.articles, key=lambda c: (c.confidence, c.content_len))

    @property
    def distinct_sources(self) -> int:
        return len({c.source for c in self.articles})

    @property
    def score(self) -> float:
        tier = max(TIER_WEIGHT.get(c.tier, 0.0) for c in self.articles)
        confidence = max(c.confidence for c in self.articles)
        section = SECTION_WEIGHT.get(self.section, 0.0)
        return tier + source_boost(self.distinct_sources) + confidence + section


def rank_stories(stories: list[Story]) -> list[Story]:
    """Highest score first; ties broken by more sources, then by lowest lead id (stable)."""
    return sorted(stories, key=lambda s: (-s.score, -s.distinct_sources, s.lead.article_id))


def select_top(stories: list[Story], n: int) -> tuple[list[Story], list[Story]]:
    """Split ranked stories into the top n and the rest."""
    ranked = rank_stories(stories)
    return ranked[:n], ranked[n:]
