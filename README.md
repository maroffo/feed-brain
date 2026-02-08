# feed-brain

AI-powered personal feed aggregator that classifies RSS articles by relevance using Claude Haiku, presents them in a clean web UI, and exports approved articles as Obsidian clippings.

## Why

RSS readers show everything. feed-brain shows what matters to *you*. Each article is scored against your interest profile by an LLM, categorized, and summarized. You browse the results, approve what's worth keeping, and it lands in your Obsidian vault as a clipping, ready for your knowledge management workflow.

## How it works

```
RSS Feeds --> Fetch --> Extract (readability) --> Classify (Haiku) --> SQLite
                                                                        |
                                                              Web UI (htmx)
                                                              +------------+
                                                              | Feed list  |
                                                              | [High]     |
                                                              | [Medium]   |
                                                              | [Low]      |
                                                              +-----+------+
                                                                    |
                                                              Article detail
                                                              + AI summary
                                                              + [Approve] [Skip]
                                                                    |
                                                              Obsidian Clippings/
```

1. **Fetch**: pulls articles from your RSS feeds using feedparser
2. **Extract**: strips ads and chrome with readability-lxml, keeps clean text
3. **Classify**: sends title + content to Claude Haiku, gets back tier (high/medium/low), category, summary, and reasoning
4. **Browse**: htmx web UI with tier filtering, article cards with badges
5. **Approve**: thumbs up creates a markdown clipping in your Obsidian vault

## Tech stack

- **Python 3.13+** with [uv](https://docs.astral.sh/uv/)
- **FastAPI** + Jinja2 + **htmx** + Pico CSS (zero build step)
- **Anthropic SDK** (Claude Haiku for classification, ~$0.001/article)
- **SQLAlchemy 2.0** + aiosqlite (async SQLite, single file DB)
- feedparser, readability-lxml, beautifulsoup4, httpx
- structlog, pydantic-settings, ruff, pytest

## Setup

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- An [Anthropic API key](https://console.anthropic.com/)

### Install

```bash
git clone https://github.com/maroffo/feed-brain.git
cd feed-brain
uv sync
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` and set your API key:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Optional settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (required) | Anthropic API key for Haiku classification |
| `CLASSIFIER_MODEL` | `claude-haiku-4-5-20251001` | Model to use for classification |
| `DB_PATH` | `./feed_brain.db` | SQLite database file path |
| `CLIPPINGS_DIR` | `~/...Obsidian.../Clippings` | Where approved clippings are saved |
| `HOST` | `127.0.0.1` | Server bind address |
| `PORT` | `8000` | Server port |
| `LOG_LEVEL` | `INFO` | Logging level |

## Usage

### Start the web UI

```bash
uv run feed-brain serve
```

Open http://localhost:8000.

### Add feeds

1. Go to http://localhost:8000/feeds
2. Add feeds manually (name + RSS URL), or import an OPML file from your existing reader

### Fetch and classify

Either click **Refresh** in the web UI, or run from the CLI:

```bash
uv run feed-brain fetch
```

This fetches all active feeds, extracts article content, and classifies each article with Haiku.

### Browse and approve

- Filter by tier: **High** / **Medium** / **Low**
- Click an article title to read the full content + AI summary
- **Thumbs up** creates a markdown clipping in your Obsidian `Clippings/` folder
- **Thumbs down** marks it as skipped

### Obsidian integration

Approved articles are saved as markdown files in your `Clippings/` directory with YAML frontmatter (title, source, author, date, tags). From there, use your existing clipping processing workflow to route them into your Second Brain.

## Classification

The classifier uses a system prompt with your interest profile to score each article:

- **Tier**: high (core interests), medium (adjacent), low (noise)
- **Category**: maps to knowledge domains (AI, development, devops, management, etc.)
- **Summary**: 2-3 sentence overview
- **Reason**: why this tier was assigned
- **Confidence**: 0.0-1.0

To customize the interest profile, edit the `SYSTEM_PROMPT` in `src/feed_brain/services/classifier.py`.

## Development

```bash
uv run ruff check .                    # Lint
uv run ruff format --check .           # Format check
uv run pytest -v                       # Tests (20 tests)
uv run feed-brain serve --reload       # Dev server with auto-reload
```

## License

MIT
