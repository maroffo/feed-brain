# ABOUTME: Deep analysis service using Anthropic for high-tier articles.
# ABOUTME: Supports Minions protocol for arXiv papers (Sonnet decomposes, Ollama reads, Sonnet synthesizes).

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import select

if TYPE_CHECKING:
    from ollama import AsyncClient

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


# --- Minions protocol for arXiv papers ---

DECOMPOSE_PROMPT = """\
You are a research paper analyst. Given a paper's title and abstract, \
generate 4-5 targeted questions that would extract the most important \
information from the full paper.

Questions should cover:
1. Main contribution and novelty
2. Methodology and approach
3. Key results and supporting evidence
4. Limitations and open questions
5. Practical implications and applications

Respond with ONLY a JSON array of question strings, no explanation.
Example: ["What is the main contribution?", "What methodology is used?"]
"""

MINION_PROMPT = """\
You are a careful research paper reader. Given a question about a paper, \
answer accurately and in detail based ONLY on the paper content provided. \
Do not speculate or add information not present in the paper.
If the paper does not contain relevant information, say so explicitly.
"""

SYNTHESIZE_PROMPT = """\
You are an expert analyst for a personal knowledge management system. \
Given question-answer pairs from reading a research paper, synthesize \
a deep analysis.

## Output Format
Respond with ONLY a JSON object (no markdown, no explanation):
{
  "summary": "<detailed 3-5 sentence summary capturing key arguments and conclusions>",
  "insights": ["<non-obvious insight 1>", "<insight 2>", "<insight 3>"],
  "money_quote": "<most impactful verbatim quote from the answers, 1-2 sentences>",
  "actionables": ["<concrete actionable takeaway 1>", "<actionable 2>"]
}

Rules for summary: go beyond surface level. Capture the core argument, evidence, \
and conclusions. Include nuance and caveats.

Rules for insights: 2-4 non-obvious observations. Things a reader might miss on \
first read. Connections to broader trends or implications.

Rules for money_quote: pick the single most memorable, insightful, or provocative \
statement from the Q&A content. Must be a direct quote from the answers.

Rules for actionables: 2-4 concrete things the reader can do, try, or apply. \
Use imperative form ("Try X", "Use Y for Z"). If purely informational, return [].
"""


def _is_arxiv(article: Article) -> bool:
    """Check if an article URL is from arXiv."""
    return "arxiv.org" in (article.url or "")


async def _decompose(title: str, abstract: str, client: AsyncAnthropic, model: str) -> list[str]:
    """Ask Sonnet to generate targeted questions about a paper.

    Returns a list of up to 5 question strings.
    """
    user_message = f"Title: {title}\n\nAbstract:\n{abstract}"

    response = await client.messages.create(
        model=model,
        max_tokens=500,
        system=DECOMPOSE_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text.strip() if response.content else "[]"

    import re

    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    questions = json.loads(text)

    if not isinstance(questions, list):
        return []

    return [str(q) for q in questions[:5]]


async def _extract_answer(
    question: str, content: str, ollama_client: AsyncClient, model: str
) -> str:
    """Ask the local model to answer a question from the full paper content."""
    user_message = f"Question: {question}\n\nPaper content:\n{content}"

    response = await ollama_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": MINION_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    return response.message.content.strip()


async def _synthesize(
    title: str,
    qa_pairs: list[tuple[str, str]],
    client: AsyncAnthropic,
    model: str,
) -> DeepAnalysis | None:
    """Ask Sonnet to synthesize Q&A pairs into a DeepAnalysis.

    Returns DeepAnalysis or None if parsing fails.
    """
    qa_text = "\n\n".join(f"Q: {question}\nA: {answer}" for question, answer in qa_pairs)
    user_message = f"Title: {title}\n\n{qa_text}"

    response = await client.messages.create(
        model=model,
        max_tokens=1500,
        system=SYNTHESIZE_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text.strip() if response.content else ""

    import re

    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()

    try:
        data = json.loads(text)
        return DeepAnalysis(
            summary=data["summary"],
            insights=data.get("insights", []),
            money_quote=data.get("money_quote", ""),
            actionables=data.get("actionables", []),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.error("synthesize_parse_error", title=title, error=str(e))
        return None


async def analyze_article_minions(
    article: Article,
    anthropic_client: AsyncAnthropic | None = None,
    ollama_client: AsyncClient | None = None,
) -> DeepAnalysis | None:
    """Analyze an arXiv paper using the Minions protocol.

    Decomposes analysis into targeted questions (Sonnet), extracts answers from
    the full paper (Ollama), then synthesizes into DeepAnalysis (Sonnet).
    Returns DeepAnalysis or None if any step fails.
    """
    settings = get_settings()

    if anthropic_client is None:
        if settings.anthropic_api_key is None:
            log.error("no_anthropic_api_key")
            return None
        anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())

    if ollama_client is None:
        from ollama import AsyncClient

        ollama_client = AsyncClient(host=settings.ollama_host)

    content = article.content or ""
    abstract = content[:500]

    # Step 1: Decompose - Sonnet generates targeted questions
    try:
        questions = await _decompose(
            article.title, abstract, anthropic_client, settings.analyzer_model
        )
    except Exception as e:
        log.warning("minions_decompose_failed", title=article.title, error=str(e))
        return None

    if not questions:
        log.warning("minions_no_questions", title=article.title)
        return None

    log.info("minions_decompose_done", title=article.title, num_questions=len(questions))

    # Step 2: Extract - Ollama answers each question from full content
    qa_pairs: list[tuple[str, str]] = []
    for question in questions:
        try:
            answer = await _extract_answer(question, content, ollama_client, settings.ollama_model)
            qa_pairs.append((question, answer))
        except Exception as e:
            log.warning(
                "minions_extract_failed",
                title=article.title,
                question=question,
                error=str(e),
            )
            return None

    log.info("minions_extract_done", title=article.title, num_answers=len(qa_pairs))

    # Step 3: Synthesize - Sonnet produces final DeepAnalysis
    try:
        result = await _synthesize(
            article.title, qa_pairs, anthropic_client, settings.analyzer_model
        )
    except Exception as e:
        log.warning("minions_synthesize_failed", title=article.title, error=str(e))
        return None

    if result:
        log.info("minions_analysis_complete", title=article.title)

    return result


async def analyze_high_tier() -> int:
    """Analyze all high-tier articles that haven't been deeply analyzed yet.

    arXiv papers use the Minions protocol (decompose/extract/synthesize).
    Other articles use direct Anthropic analysis. Falls back to direct
    analysis if Minions fails.

    Returns the number of articles analyzed.
    """
    settings = get_settings()
    if settings.anthropic_api_key is None:
        log.error("no_anthropic_api_key")
        return 0

    from ollama import AsyncClient

    anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    ollama_client = AsyncClient(host=settings.ollama_host)
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
            analysis = None

            if _is_arxiv(article):
                log.info("using_minions_protocol", title=article.title, url=article.url)
                analysis = await analyze_article_minions(
                    article,
                    anthropic_client=anthropic_client,
                    ollama_client=ollama_client,
                )
                if analysis is None:
                    log.info(
                        "minions_fallback_to_direct",
                        title=article.title,
                    )

            if analysis is None:
                analysis = await analyze_article(article, client=anthropic_client)

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
