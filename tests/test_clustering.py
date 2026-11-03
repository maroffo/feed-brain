# ABOUTME: Tests for per-section story clustering: id normalization, fallbacks, section fan-out.
# ABOUTME: Gemini is faked; normalize_clusters is pure and covers hallucinated/duplicate/missing ids.

from feed_brain.ai.gemini import GeminiError
from feed_brain.services.clustering import (
    ClusterOut,
    ClusterResponse,
    cluster_candidates,
    normalize_clusters,
)
from feed_brain.services.ranking import Candidate


def _c(article_id, section="tech", source="A"):
    return Candidate(
        article_id=article_id,
        title=f"Title {article_id}",
        url=f"https://x/{article_id}",
        source=source,
        tier="medium",
        section=section,
        confidence=0.5,
        summary=f"Summary {article_id}.",
        content_len=10,
    )


def test_normalize_drops_unknown_dedups_and_adds_singletons():
    candidates = [_c(1), _c(2), _c(3), _c(4)]
    raw = ClusterResponse(
        stories=[
            ClusterOut(article_ids=[1, 2, 99], headline="Uno e due"),
            ClusterOut(article_ids=[2, 2], headline="Duplicato"),
            ClusterOut(article_ids=[3], headline="  "),
        ]
    )
    stories = normalize_clusters(raw, candidates, "tech")
    assert [[c.article_id for c in s.articles] for s in stories] == [[1, 2], [3], [4]]
    assert [s.headline for s in stories] == ["Uno e due", "Title 3", "Title 4"]
    assert all(s.section == "tech" for s in stories)


class FakeGemini:
    def __init__(self, fail_sections=()):
        self.prompts = []
        self.fail_sections = set(fail_sections)

    async def generate(self, prompt, system, schema):  # noqa: ARG002 - protocol signature
        assert schema is ClusterResponse
        self.prompts.append(prompt)
        ids = [int(line.split("]")[0][1:]) for line in prompt.splitlines() if line.startswith("[")]
        if any(f"Section: {s}" in prompt for s in self.fail_sections):
            raise GeminiError("down")
        return ClusterResponse(stories=[ClusterOut(article_ids=ids, headline="Tutto insieme")])


async def test_cluster_candidates_one_call_per_section():
    candidates = [
        _c(1, "tech"),
        _c(2, "tech", source="B"),
        _c(3, "security"),
    ]
    gemini = FakeGemini()

    stories = await cluster_candidates(candidates, gemini, concurrency=2)

    assert len(gemini.prompts) == 2
    by_section = {s.section: [c.article_id for c in s.articles] for s in stories}
    assert by_section == {"tech": [1, 2], "security": [3]}
    tech_prompt = next(p for p in gemini.prompts if "Section: tech" in p)
    assert "[1] Title 1 (A): Summary 1." in tech_prompt


async def test_cluster_failure_falls_back_to_singletons():
    candidates = [_c(1, "tech"), _c(2, "tech")]
    stories = await cluster_candidates(
        candidates, FakeGemini(fail_sections={"tech"}), concurrency=1
    )
    assert [[c.article_id for c in s.articles] for s in stories] == [[1], [2]]
    assert [s.headline for s in stories] == ["Title 1", "Title 2"]
