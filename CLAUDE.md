# feed-brain

**Repo:** https://github.com/maroffo/feed-brain

Personal RSS digest: a daily Italian bulletin and a Sunday recap, built by a Cloud Run Job from an OPML and from paid newsletters forwarded to an SES inbox, emailed and published as a static page; a launchd sync brings notes and top articles into the Obsidian vault. Modeled on BehindBarsPulse, whose Cloud SQL instance, VPC connector and SES/Gemini secrets it shares (by name only).

## Stack

Python 3.13 + uv, SQLAlchemy 2 async + asyncpg, Alembic, Postgres 15, Gemini Flash (`google-genai`, structured output), Anthropic Opus 5.5 (deep analysis, optional), Jinja2, AWS SES (SMTP out; receiving into S3, eu-west-1), boto3 with keyless Google → AWS web identity, Google Cloud Storage, structlog (JSON when not a TTY), Terraform (`infra/`, own GCS state).

## Structure

```
src/feed_brain/
  __main__.py          # CLI: run, collect, bulletin, weekly, sync-vault, migrate, analyze, ...
  config.py            # Pydantic Settings (env / .env)
  models.py            # Tier, Section (12), Category (vault), TriageResult, DeepAnalysis, BulletinData, WeeklyData
  interests.md         # interest profile injected into the triage prompt (edit freely)
  textutil.py          # clean_text: strip NUL/control chars before Postgres
  ai/gemini.py         # async structured output + retries (429/5xx, transport, bad JSON)
  ai/prompts.py        # clustering, bulletin and weekly prompts (Italian output)
  db/models.py         # FeedSource, Article, Bulletin, WeeklyDigest
  db/session.py        # asyncpg engine (pool 2, no overflow, pre-ping)
  db/migrate.py        # programmatic Alembic; migrations in db/migrations/versions
  services/
    fetcher.py extractor.py pdf.py opml.py    # ingestion (OPML sync, fetch, extract)
    inbox.py           # newsletter inbox: S3 mails → articles (offline link decoding, no tokens kept)
    triage.py          # Gemini triage on snapshots, concurrent, per-article commits
    clustering.py      # one Gemini call per section, normalize_clusters
    ranking.py         # pure: score, lead, top stories, radar
    analyzer.py        # Opus 5.5 (effort high, fallbacks="default"), Minions for arXiv
    bulletin.py        # generate_bulletin: idempotent per day, atomic --force
    weekly.py          # due_week + generate_weekly
    render.py          # Jinja HTML (email-safe) + Markdown notes; safe_url/md filters
    storage.py         # LocalStorage / GcsStorage
    delivery.py mailer.py outbox.py   # publish + index, SES email, vault outbox
    vault_sync.py      # Mac side: outbox → vault (URL dedup), OPML push
    pipeline.py        # run_daily: isolated steps, failed steps → non-zero exit
    integrator.py      # vault entry building + append with URL dedup
  templates/           # _base, _macros, bulletin, weekly, index (.html) + bulletin/weekly (.md)
infra/                 # Terraform: job, scheduler, buckets, DB/user, IAM; inbox.tf (SES receiving, S3, AWS role)
ops/launchd/           # sync-vault agent
```

## Commands

```bash
docker compose up -d db && uv run feed-brain migrate   # local Postgres on :5433
uv run feed-brain run --dry-run                        # whole day locally (./site, ./outbox)
uv run feed-brain bulletin [--force]                   # today's bulletin → ./site
make check                                             # ruff, format, ty, unit tests
make test-e2e                                          # integration tests on docker Postgres
```

Deployment runbook: README.md.

## Conventions

- 2-line ABOUTME headers on all files (templates use `{#- ABOUTME: ... -#}`)
- async everywhere; one AsyncSession per coroutine; LLM calls run on plain snapshots, DB writes in the main coroutine
- Everything that reaches Postgres from feeds or LLMs goes through `clean_text`
- Tests: SQLite in-memory via the `session_factory` fixture (seeds `feed_brain.db.session._session_factory`); Postgres-only behavior in `tests/integration`
- Email-sourced articles (`feed_type` "email") are paid content: the public page (`public=True` render) shows their stories as title + link only; email and vault keep everything
- Feed and mail text reaches the LLMs between `<article>` tags, declared untrusted (the inbox is open)
- Generated content in Italian; code, comments and prompts in English (bulletin prompts are Italian)
