# ABOUTME: Deep analysis service using Anthropic for high-tier articles.
# ABOUTME: Extracts detailed summary, insights, money quote, and actionables.

import json

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import select

from feed_brain.config import get_settings
from feed_brain.db.models import Article
from feed_brain.db.session import get_session_factory
from feed_brain.models import ArticleStatus, DeepAnalysis, Tier

log = structlog.get_logger()

SYSTEM_PROMPT = """\
You are an expert analyst for a personal knowledge management system. \
Provide deep analysis of high-value articles.

## Output Format
Respond with ONLY a JSON object (no markdown, no explanation):
{
  "summary": "<detailed 3-5 sentence summary capturing key arguments and conclusions>",
  "insights": ["<non-obvious insight 1>", "<insight 2>", "<insight 3>"],
  "money_quote": "<most impactful verbatim quote from the article, 1-2 sentences>",
  "actionables": ["<concrete actionable takeaway 1>", "<actionable 2>"]
}

Rules for summary: go beyond surface level. Capture the core argument, evidence, \
and conclusions. Include nuance and caveats.

Rules for insights: 2-4 non-obvious observations. Things a reader might miss on \
first read. Connections to broader trends or implications.

Rules for money_quote: pick the single most memorable, insightful, or provocative \
sentence from the article text. Must be a direct quote, not a paraphrase.

Rules for actionables: 2-4 concrete things the reader can do, try, or apply. \
Use imperative form ("Try X", "Use Y for Z"). If purely informational, return [].
"""


async def analyze_article(
    article: Article, client: AsyncAnthropic | None = None
) -> DeepAnalysis | None:
    """Run deep analysis on a single article using Anthropic.

    Returns DeepAnalysis or None if analysis fails.
    """
    settings = get_settings()
    if client is None:
        if settings.anthropic_api_key is None:
            log.error("no_anthropic_api_key")
            return None
        client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())

    content_preview = (article.content or "")[:6000]
    user_message = (
        f"Title: {article.title}\n"
        f"Author: {article.author or 'Unknown'}\n\n"
        f"Content:\n{content_preview}"
    )

    try:
        response = await client.messages.create(
            model=settings.analyzer_model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        text = response.content[0].text.strip() if response.content else ""
        log.debug("analyzer_raw_response", text=text[:200])

        # Strip markdown code fences if present
        import re

        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        data = json.loads(text)

        result = DeepAnalysis(
            summary=data["summary"],
            insights=data.get("insights", []),
            money_quote=data.get("money_quote", ""),
            actionables=data.get("actionables", []),
        )

        log.info("article_analyzed", title=article.title)
        return result

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.error("analysis_parse_error", title=article.title, error=str(e))
        return None
    except Exception as e:
        log.error("analysis_error", title=article.title, error=str(e))
        return None


async def analyze_high_tier() -> int:
    """Analyze all high-tier articles that haven't been deeply analyzed yet.

    Returns the number of articles analyzed.
    """
    settings = get_settings()
    if settings.anthropic_api_key is None:
        log.error("no_anthropic_api_key")
        return 0

    client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    session_factory = get_session_factory()
    analyzed = 0

    async with session_factory() as session:
        result = await session.execute(
            select(Article).where(
                Article.tier == Tier.HIGH,
                Article.status == ArticleStatus.TRIAGED,
                Article.deep_summary.is_(None),
                Article.content.isnot(None),
            )
        )
        articles = result.scalars().all()

        for article in articles:
            analysis = await analyze_article(article, client=client)
            if analysis:
                article.deep_summary = analysis.summary
                article.deep_insights = json.dumps(analysis.insights)
                article.money_quote = analysis.money_quote
                article.actionables = json.dumps(analysis.actionables)
                article.status = ArticleStatus.ANALYZED
                analyzed += 1

        await session.commit()

    log.info("analysis_complete", analyzed=analyzed, total=len(articles))
    return analyzed
