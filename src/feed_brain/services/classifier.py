# ABOUTME: Article classification service using Anthropic Haiku.
# ABOUTME: Scores articles by relevance tier, assigns category, generates summary.

import json
import re
from datetime import UTC, datetime

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import select

from feed_brain.config import get_settings
from feed_brain.db.models import Article
from feed_brain.db.session import get_session_factory
from feed_brain.models import Category, ClassificationResult, Tier

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

## Output Format
Respond with ONLY a JSON object (no markdown, no explanation):
{"tier": "high|medium|low", "category": "<category>", "summary": "<2-3 sentence summary>", \
"reason": "<why this tier>", "confidence": 0.0-1.0, \
"money_quote": "<most impactful verbatim quote from the article, 1-2 sentences>", \
"actionables": ["<concrete actionable takeaway 1>", "<actionable 2>", ...]}

Rules for money_quote: pick the single most memorable, insightful, or provocative sentence \
from the article text. Must be a direct quote, not a paraphrase. If no standout quote exists, \
use the most informative sentence.

Rules for actionables: 2-4 concrete things the reader can do, try, or apply based on the \
article. Use imperative form ("Try X", "Use Y for Z", "Consider switching to..."). \
If the article is purely informational with no actionable content, return an empty array.
"""


async def classify_article(
    article: Article, client: AsyncAnthropic | None = None
) -> ClassificationResult | None:
    """Classify a single article using Haiku.

    Returns ClassificationResult or None if classification fails.
    """
    settings = get_settings()
    if client is None:
        if settings.anthropic_api_key is None:
            log.error("no_anthropic_api_key")
            return None
        client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())

    content_preview = (article.content or "")[:3000]
    user_message = f"Title: {article.title}\nAuthor: {article.author or 'Unknown'}\n\nContent:\n{content_preview}"

    try:
        response = await client.messages.create(
            model=settings.classifier_model,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        text = response.content[0].text.strip() if response.content else ""
        log.debug("classifier_raw_response", text=text[:200], stop_reason=response.stop_reason)
        # Strip markdown code fences if present (e.g. ```json ... ```)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        data = json.loads(text)
        result = ClassificationResult(
            tier=Tier(data["tier"]),
            category=Category(data["category"]),
            summary=data["summary"],
            reason=data["reason"],
            money_quote=data.get("money_quote", ""),
            actionables=data.get("actionables", []),
            confidence=float(data["confidence"]),
        )
        log.info(
            "article_classified",
            title=article.title,
            tier=result.tier,
            category=result.category,
        )
        return result

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.error("classification_parse_error", title=article.title, error=str(e))
        return None
    except Exception as e:
        log.error("classification_error", title=article.title, error=str(e))
        return None


async def classify_unclassified() -> int:
    """Classify all articles that haven't been classified yet.

    Returns the number of articles classified.
    """
    settings = get_settings()
    if settings.anthropic_api_key is None:
        log.error("no_anthropic_api_key")
        return 0

    client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    session_factory = get_session_factory()
    classified = 0

    async with session_factory() as session:
        result = await session.execute(
            select(Article).where(
                Article.classified_at.is_(None),
                Article.content.isnot(None),
            )
        )
        articles = result.scalars().all()

        for article in articles:
            classification = await classify_article(article, client)
            if classification:
                article.summary = classification.summary
                article.tier = classification.tier.value
                article.category = classification.category.value
                article.reason = classification.reason
                article.confidence = classification.confidence
                article.money_quote = classification.money_quote
                article.actionables = json.dumps(classification.actionables)
                article.classified_at = datetime.now(UTC)
                classified += 1

        await session.commit()

    log.info("classification_complete", classified=classified, total=len(articles))
    return classified
