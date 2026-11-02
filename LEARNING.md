# LEARNING.md

## Project Overview

feed-brain is a personal RSS aggregator that triages articles with a local LLM (Ollama), runs deep analysis on the best ones with Anthropic, and pushes results directly into the Obsidian vault. Started as a FastAPI web app, evolved into a pure CLI pipeline.

## Architecture

```mermaid
graph LR
    OPML[OPML Import] --> DB[(SQLite)]
    RSS[RSS Feeds] -->|httpx async| Fetcher
    Fetcher -->|readability / trafilatura / PyMuPDF| DB
    DB -->|status: new| Triage[Ollama Triage]
    Triage -->|status: triaged| DB
    DB -->|tier: high| Analyzer[Anthropic Analysis]
    Analyzer -->|status: analyzed| DB
    DB -->|threshold logic| Integrator[Vault Writer]
    Integrator -->|.md files| Vault[Obsidian Second Brain]
```

Pipeline state machine: `new → triaged → analyzed → integrated | error`

## Tech Stack & Decisions

| Technology | Why | Trade-offs |
|-----------|-----|------------|
| Ollama (llama3.2:3b) | Free, fast, local triage. Pydantic `format=` for structured output | Needs Ollama running locally, less accurate than cloud models |
| Anthropic Sonnet | Deep analysis on high-tier only (cost control) | API key required, cost per call |
| PyMuPDF | Best two-column PDF extraction (`get_text(sort=True)`) | Binary dependency, large package |
| trafilatura | Fallback when readability-lxml produces garbage | Slower than readability, sometimes too aggressive at stripping |
| tenacity | Retry on Ollama parse failures (structured output isn't perfect) | Adds latency on bad responses |
| Direct vault write | No dependency on obsidian CLI, simpler than API | No Obsidian metadata/plugin hooks, just raw files |
| Explicit status enum | Clear pipeline state vs. inferring from nullable timestamps | Extra column, but much easier to query and debug |

## Lessons Learned

### 2026-03-09: The Great Web UI Purge

**Context:** feed-brain had a full FastAPI + htmx web UI with Pico CSS, article cards, feed management, approval workflow. ~1200 lines of web code (routes, templates, CSS).

**Problem:** For a personal tool running locally, the web UI was overkill. The real workflow is: fetch articles, let AI sort them, push the good ones to Obsidian. Nobody needs a browser for that.

**Solution:** Deleted the entire `web/` directory, all templates, CSS, routes. Replaced with 7 CLI commands that map directly to pipeline stages. Net result: -1800 lines deleted, +485 added.

**Takeaway:** Build the simplest interface that matches actual usage. A CLI with `run --auto` is worth more than a polished web UI nobody opens.

### 2026-03-09: AsyncSession Is Not Thread-Safe (Or Concurrency-Safe)

**Context:** Rewrote the fetcher to use `asyncio.gather` with a semaphore for parallel feed fetching. Initially passed a single `AsyncSession` to all concurrent tasks.

**Problem:** Gemini review caught it: SQLAlchemy's `AsyncSession` is stateful and NOT safe for concurrent use. Multiple tasks adding articles to the same session would cause `RuntimeError` or transaction corruption.

**Solution:** Each `_bounded_fetch` task now creates its own session from the factory:
```python
async def _bounded_fetch(source: FeedSource) -> int:
    async with semaphore, session_factory() as session:
        count = await _fetch_single_feed(session, source, settings)
        await session.commit()
        return count
```

**Takeaway:** `AsyncSession` is async, not concurrent. One session per concurrent task, always. The `async_sessionmaker` factory is designed exactly for this pattern.

### 2026-03-09: ALTER TABLE in a Single Transaction Is a Trap

**Context:** Wrote idempotent migrations as a loop of ALTER TABLE statements wrapped in a single `engine.begin()` transaction.

**Problem:** In SQLite (and most DBs), if one ALTER TABLE fails (e.g., column already exists), the entire transaction is invalidated. Subsequent statements in the loop, including the backfill UPDATEs, would crash with `PendingRollbackError`.

**Solution:** Each ALTER TABLE gets its own transaction:
```python
for table, column, col_type in migrations:
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
    except Exception:
        pass  # Column already exists
```

**Takeaway:** Idempotent migrations need idempotent transactions. One DDL statement = one transaction, especially when you're catching exceptions to skip already-applied changes.

### 2026-03-09: CPU-Bound Work Inside Async Functions Kills Concurrency

**Context:** The extractor calls readability-lxml, BeautifulSoup, and trafilatura, all CPU-bound parsing. These were called directly inside `async def extract_content()`.

**Problem:** CPU-bound work in an async function blocks the event loop. With 10 concurrent fetchers, they'd all stall waiting for one feed's content extraction to finish.

**Solution:** Wrap CPU-bound parsing in `asyncio.to_thread()`:
```python
cleaned = await asyncio.to_thread(_extract_with_readability, html)
```

**Takeaway:** `async` doesn't magically make CPU work concurrent. If it's not I/O, it needs a thread. Rule of thumb: if the function doesn't `await` anything, it probably needs `to_thread`.

## Pitfalls & Gotchas

- **feedparser.parse with a URL** will make its own HTTP request, bypassing your async client. Download XML with httpx first, then `feedparser.parse(xml_string)`.
- **Ollama structured output** (`format=` parameter) works well but isn't bulletproof. The 3b model occasionally produces malformed JSON, hence tenacity retry.
- **SQLite ALTER TABLE** cannot add columns with NOT NULL constraints without defaults. Always use `DEFAULT` in migration DDL.
- **trafilatura import** is slow (~200ms). Lazy-import it only in the fallback path.

## Best Practices Discovered

- **Pipeline status field > nullable timestamps**: `Article.status` is explicit and queryable. No more guessing "is it classified?" by checking `classified_at IS NOT NULL`.
- **Semaphore + session-per-task**: Clean pattern for bounded concurrent DB work in async Python.
- **Download-then-parse**: Decouple HTTP fetching (async, can be concurrent) from parsing (CPU-bound, needs thread). Don't let libraries do their own I/O inside your async code.
- **Structured output with Pydantic schema**: Ollama's `format=Model.model_json_schema()` is cleaner than prompt-engineering JSON output and parsing it yourself.

## 2026-11-02: Cloud newsletter (Cloud Run Job, Postgres, Gemini, Opus)

feed-brain became a cloud service modeled on BehindBarsPulse: a daily Italian bulletin and a Sunday recap, built by a Cloud Run Job, emailed and published as a static page, with a launchd sync back into the vault. The architecture section above describes the pre-2026-11 CLI; README.md and CLAUDE.md describe the current one.

| Decision | Why | Trade-off |
|----------|-----|-----------|
| Postgres (shared Cloud SQL instance, own DB) + Alembic | Cloud Run disk is ephemeral; marginal cost zero | Shared db-f1-micro: pool 2, no overflow |
| Gemini Flash for triage/clustering/writing | Ollama can't run on Cloud Run; structured output is reliable | Preview model names churn (env-overridable) |
| Opus 5.5 only on the 10 top-story leads | Depth where it matters, cost bounded | No deep analysis until the Anthropic secret exists |
| One clustering call per section + pure ranking | One huge call drops/hallucinates ids; ranking stays testable | Cross-section stories are not merged |
| Self-contained BulletinData JSON per issue | Render and weekly never re-join articles | Schema changes need defaults for old rows |
| Vault via GCS outbox + launchd sync | The job can't reach the iCloud vault | Notes arrive when the Mac is on |

### Lessons

- **LLMs emit NUL bytes.** Gemini returned `\x00` inside an Italian summary; Postgres rejected the UPDATE and, because the loop shared one session, the whole triage aborted. Everything from feeds or LLMs now goes through `clean_text`, and triage works on immutable snapshots with per-article commit/rollback. The unit tests on SQLite could not have caught it: only the real run did.
- **Patch the cache, not the function.** A test fixture that monkeypatched `get_session_factory` silently missed modules that imported the function by name, so tests hit the real local Postgres. Seeding the module-level `_session_factory` fixes every import style.
- **Gmail clips HTML over 102 KB.** The first real bulletin rendered at 158 KB. A per-section cap plus moving repeated inline styles into a `<style>` block brought it to 69 KB.
- **The old integration rule did not survive the new profile.** "High tier, confidence ≥ 0.8" queued 70 vault entries in one day once the profile matched the OPML; entries are now limited to the top-story leads.
- **Destructive steps go last.** `--force` used to delete the old issue before regenerating; a failed regeneration lost the bulletin. The replacement now happens in the same transaction as the insert.
- **Feed URLs are untrusted.** `href` values pass through `safe_url` (http/https only) and Markdown links escape brackets and parentheses.
- **"Blocked from the cloud" is three different problems.** Tested from a residential IP, through NordVPN and from Cloud Run: CISA rejected the browser User-Agent on Python's TLS stack (an honest bot UA gets 200 everywhere), L'Europeista serves a 13.7 MB feed slower than the old 15 s timeout, and only Dark Reading and Europae block datacenter IPs (VPN exits included). A proxy would have fixed none of the first two. Feeds now send `feed-brain/0.1 (+repo)`, pages keep the browser UA, and the proxy fallback also covers network errors.
