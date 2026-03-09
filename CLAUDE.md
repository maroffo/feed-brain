# feed-brain

**Repo:** https://github.com/maroffo/feed-brain

AI-powered personal feed aggregator. Triages RSS articles locally with Ollama, runs deep analysis on high-tier articles with Anthropic, and integrates directly into the Obsidian Second Brain vault.

## Stack

Python 3.13, Ollama (llama3.2:3b), Anthropic SDK (Sonnet), SQLAlchemy + aiosqlite (SQLite), PyMuPDF, trafilatura, structlog, feedparser + readability-lxml.

## Structure

```
src/feed_brain/
  __main__.py       # CLI: fetch, triage, analyze, integrate, run, list, import-opml
  config.py         # Pydantic Settings
  models.py         # Pydantic schemas (TriageResult, DeepAnalysis, ArticleStatus)
  db/models.py      # SQLAlchemy ORM (FeedSource, Article)
  db/session.py     # Engine + async session + migrations
  services/
    fetcher.py      # Concurrent RSS feed fetching (asyncio.gather + semaphore)
    extractor.py    # Content extraction (readability + trafilatura fallback + PDF routing)
    triage.py       # Ollama triage with structured output + tenacity retry
    analyzer.py     # Anthropic deep analysis (high-tier only)
    pdf.py          # PyMuPDF extraction, arxiv URL handling
    integrator.py   # Second Brain vault writer
    opml.py         # OPML feed import parser
```

## Commands

```bash
uv run feed-brain fetch              # Fetch new articles from all feeds
uv run feed-brain triage             # Ollama classifies unclassified articles
uv run feed-brain analyze            # Anthropic deep analysis on high-tier
uv run feed-brain integrate [--auto] # Push to Second Brain
uv run feed-brain run [--auto]       # Full pipeline: fetch→triage→analyze→integrate
uv run feed-brain list [--tier X]    # Show recent articles
uv run feed-brain import-opml <file> # Import feeds from OPML
uv run ruff check . && uv run ruff format --check .  # Lint
uv run pytest                        # Tests
```

## Conventions

- 2-line ABOUTME headers on all files
- structlog for logging
- Pydantic Settings for config (.env)
- async everywhere (SQLAlchemy async, httpx async)
- Article status field tracks pipeline state: new → triaged → analyzed → integrated | error
