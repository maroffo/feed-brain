# ABOUTME: Groups a day's candidates into stories, one Gemini call per section (threat_radar excluded).
# ABOUTME: normalize_clusters repairs LLM output: unknown ids dropped, duplicates first-wins, gaps → singletons.

import asyncio
from collections import defaultdict
from typing import Protocol

import structlog
from pydantic import BaseModel

from feed_brain.ai.gemini import GeminiError
from feed_brain.ai.prompts import CLUSTER_PROMPT
from feed_brain.services.ranking import THREAT_RADAR, Candidate, Story
from feed_brain.textutil import clean_text

log = structlog.get_logger()


class ClusterOut(BaseModel):
    article_ids: list[int]
    headline: str


class ClusterResponse(BaseModel):
    stories: list[ClusterOut]


class ClusterLLM(Protocol):
    async def generate(
        self, prompt: str, system: str, schema: type[ClusterResponse]
    ) -> ClusterResponse:
        """Return the model answer parsed into `schema`."""
        ...


def normalize_clusters(
    raw: ClusterResponse, candidates: list[Candidate], section: str
) -> list[Story]:
    """Turn LLM clusters into Stories covering every candidate exactly once."""
    by_id = {c.article_id: c for c in candidates}
    seen: set[int] = set()
    stories: list[Story] = []
    for cluster in raw.stories:
        ids = [i for i in dict.fromkeys(cluster.article_ids) if i in by_id and i not in seen]
        if not ids:
            continue
        seen.update(ids)
        articles = [by_id[i] for i in ids]
        story = Story(section=section, articles=articles, headline="")
        story.headline = (clean_text(cluster.headline) or "").strip() or story.lead.title
        stories.append(story)
    stories.extend(
        Story(section=section, articles=[c], headline=c.title)
        for c in candidates
        if c.article_id not in seen
    )
    return stories


async def _cluster_section(
    section: str, candidates: list[Candidate], gemini: ClusterLLM
) -> list[Story]:
    lines = "\n".join(f"[{c.article_id}] {c.title} ({c.source}): {c.summary}" for c in candidates)
    try:
        raw = await gemini.generate(
            f"Section: {section}\n\n{lines}", CLUSTER_PROMPT, ClusterResponse
        )
    except GeminiError as e:
        log.warning("clustering_failed", section=section, error=str(e))
        raw = ClusterResponse(stories=[])
    stories = normalize_clusters(raw, candidates, section)
    log.info("section_clustered", section=section, articles=len(candidates), stories=len(stories))
    return stories


async def cluster_candidates(
    candidates: list[Candidate], gemini: ClusterLLM, concurrency: int
) -> list[Story]:
    """Cluster every non-radar section concurrently; returns stories of all sections."""
    by_section: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        if c.section != THREAT_RADAR:
            by_section[c.section].append(c)

    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(section: str, group: list[Candidate]) -> list[Story]:
        async with semaphore:
            return await _cluster_section(section, group, gemini)

    results = await asyncio.gather(*(_bounded(s, g) for s, g in by_section.items()))
    return [story for stories in results for story in stories]
