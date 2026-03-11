# ABOUTME: Article triage service using Ollama with structured output.
# ABOUTME: Classifies articles by tier/category/confidence using local LLM.

import json
from datetime import UTC, datetime

import structlog
from ollama import AsyncClient
from sqlalchemy import select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from feed_brain.config import Settings, get_settings
from feed_brain.db.models import Article
from feed_brain.db.session import get_session_factory
from feed_brain.models import ArticleStatus, Category, Tier, TriageResult

log = structlog.get_logger()

SYSTEM_PROMPT = """\
You are a content classifier for a personal knowledge management system. \
Classify articles based on the reader's interest profile.

## Interest Profile

**High interest (tier: high):**
- AI agents, context engineering, MCP, memory systems, LLM tooling
- Claude Code, skills, subagents, hooks, AI-assisted development
- Go concurrency, architecture patterns, performance
- Engineering management: productivity frameworks (SPACE, DORA), technical debt strategies
- DevOps: Kubernetes, Terraform, observability, cloud architecture

**Medium interest (tier: medium):**
- Python tooling, CLI tools, terminal workflows
- Docker, containers, security hardening
- Software architecture, API design, distributed systems
- European politics, Italian politics, geopolitics, trade policy
- Leadership, team practices, hiring

**Low interest (tier: low):**
- Basic tutorials, beginner guides
- Marketing, business strategy (unless novel frameworks)
- Media, culture, health (unless breakthrough research)
- Hype pieces, AI doomerism, sales pitches

## Quality Signals (boost tier)
- Original analysis with data or experience
- Actionable frameworks or mental models
- Practical papers with reproducible results
- Contrarian views backed by evidence

## Anti-patterns (lower tier)
- Clickbait, listicles without depth
- Rehashed news without analysis
- Product announcements disguised as articles
- Tutorial-style without novel insight

## Categories
- ai_agents: AI, LLM, agents, prompts, context engineering, MCP
- claude_code: Claude Code CLI, skills, subagents, hooks
- development: Go, Python, Java, CLI, data tools, APIs, libraries
- devops_cloud: Docker, K8s, Terraform, cloud, monitoring, security
- engineering_management: Leadership, productivity, team practices, tech debt
- politics_economics: Politics, economics, geopolitics, EU/IT policy
- marketing: Marketing, business strategy, growth
- media_culture: Media, culture, literature, journalism
- health_science: Health, science, medicine, research

## Output
Return a JSON object with these exact fields:
- tier: "high", "medium", or "low"
- category: one of the categories above
- confidence: 0.0 to 1.0
- summary: 2-3 sentence summary
- reason: why this tier was assigned
"""


class TriageParseError(Exception):
    """Raised when Ollama response fails to parse as TriageResult."""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception_type(TriageParseError),
    reraise=True,
)
async def triage_article(
    article: Article, client: AsyncClient | None = None, settings: Settings | None = None
) -> TriageResult | None:
    """Triage a single article using Ollama with structured output.

    Returns TriageResult or None if triage fails after retries.
    """
    settings = settings or get_settings()
    client = client or AsyncClient(host=settings.ollama_host)

    content_preview = (article.content or "")[:3000]
    user_message = (
        f"Title: {article.title}\n"
        f"Author: {article.author or 'Unknown'}\n\n"
        f"Content:\n{content_preview}"
    )

    try:
        response = await client.chat(
            model=settings.ollama_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            format=TriageResult.model_json_schema(),
        )

        text = response.message.content.strip()
        log.debug("triage_raw_response", text=text[:200])

        data = json.loads(text)
        result = TriageResult(
            tier=Tier(data["tier"]),
            category=Category(data["category"]),
            confidence=float(data["confidence"]),
            summary=data["summary"],
            reason=data["reason"],
        )

        log.info(
            "article_triaged",
            title=article.title,
            tier=result.tier,
            category=result.category,
            confidence=result.confidence,
        )
        return result

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.warning("triage_parse_error", title=article.title, error=str(e))
        raise TriageParseError(str(e)) from e
    except TriageParseError:
        raise
    except Exception as e:
        log.error("triage_error", title=article.title, error=str(e))
        return None


async def triage_new_articles() -> int:
    """Triage all articles with status 'new' that have content.

    Returns the number of articles triaged.
    """
    settings = get_settings()
    client = AsyncClient(host=settings.ollama_host)
    session_factory = get_session_factory()
    triaged = 0

    async with session_factory() as session:
        result = await session.execute(
            select(Article).where(
                Article.status == ArticleStatus.NEW,
                Article.content.isnot(None),
            )
        )
        articles = result.scalars().all()

        for article in articles:
            try:
                triage = await triage_article(article, client=client, settings=settings)
            except TriageParseError:
                log.error("triage_failed_after_retries", title=article.title)
                article.status = ArticleStatus.ERROR
                continue

            if triage:
                article.summary = triage.summary
                article.tier = triage.tier.value
                article.category = triage.category.value
                article.reason = triage.reason
                article.confidence = triage.confidence
                article.classified_at = datetime.now(UTC)
                article.status = ArticleStatus.TRIAGED
                triaged += 1
            else:
                article.status = ArticleStatus.ERROR

            await session.commit()

    log.info("triage_complete", triaged=triaged, total=len(articles))
    return triaged
