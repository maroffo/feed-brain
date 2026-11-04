# feed-brain

A personal RSS digest. Every morning a Cloud Run Job reads the feeds from an OPML file and the newsletters forwarded to its inbox, triages each new article against an interest profile, groups the articles that cover the same story, ranks the stories, writes an Italian bulletin and emails it. A static archive page is published at the same time. On Sunday a weekly recap follows. A small sync on the Mac brings the bulletin, the recap and the best articles into an Obsidian vault.

## How it works

```
Cloud Scheduler 06:30 Europe/Rome
  └─ Cloud Run Job `feed-brain run`
       1. collect   OPML (GCS) → fetch + extract; newsletter inbox (SES → S3) → articles
                    → triage (Gemini Flash: tier, section, vault category)
       2. editions  for the main issue, then each thematic one (Ingegneria, AI per sviluppatori,
                    Ricerca, Radar minacce): candidates (last 36h, high/medium, the edition's sections)
                    → cluster per section (Gemini) → rank (tier + multi-source boost + confidence)
                    → deep analysis of the first 3 top leads (Claude Opus 5.5, optional)
                    → an essay per top story from the text of all its sources (Opus, optional)
                    → write (Gemini) → store → publish page + email + vault outbox; empty: skipped
       3. weekly    on Sunday (or catch-up Mon-Sat) from the week's bulletins

Cloud Run service `feed-brain serve` (public, scales to zero)
  └─ vote links of the email → story_votes; essay links → private essay page (read-only)

Mac (launchd, hourly)
  └─ `feed-brain sync-vault`: vault outbox (GCS) → Obsidian topic notes + Digests/, local OPML → GCS
```

- **Sections**: 13, derived from the OPML folders (security, threat radar, AI, AI for developers, tech, cloud/DevOps, software, management, research, politics, economy, culture, sport). Triage assigns them per article, using feed name and folder as a hint. AI is split: `ai` for AI as news, `ai_dev` for coding agents, AI coding tools, MCP and context engineering.
- **Editions**: every section belongs to exactly one edition (`EDITIONS` in `models.py`), and each edition is its own email from the same pipeline, every morning:
  - **Bollettino** (`daily`): politics/economy, then culture/sport, then security, AI and tech; 5 top stories, an essay on each.
  - **Ingegneria** (cloud/DevOps, software, management), **AI per sviluppatori** (`ai_dev`), **Ricerca** (papers), **Radar minacce** (single CVEs and ransomware claims): 3 top stories, an essay only for stories with at least 2 sources (single-source technical stories get the Opus notes instead).
  - An edition with no candidate story stores nothing and sends nothing. Pages are `bulletins/<date>.html` (daily) and `bulletins/<date>-<edition>.html`; vault notes are `feed-brain <date>` and `feed-brain <date> <Edition>`. The weekly recap reads the daily edition only.
- **Issue shape**: 'Il punto' as 3-5 key points, 5 top stories (3 in a thematic edition), 3 'In breve' items per section (forwarded newsletters always shown). Analyzed stories show the source's money quote; insights and to-dos only when the analysis finds something actionable for the reader. Writing is telegraphic: no moral closer, no outlet names in the prose.
- **Interest profile**: `src/feed_brain/interests.md`, plain Markdown injected into the triage prompt. Edit it to change what counts as high, medium or low.
- **Stack**: `src/feed_brain/stack.md`, the technologies the reader runs, injected into the triage prompt next to the interests. A CVE on the stack is medium (high when actively exploited); off the stack it stays low and out of the Radar minacce.
- **Ranking**: `score = tier weight (high 2, medium 1) + source boost (2 feeds 0.5, 3 feeds 0.8, 4+ 1.0) + confidence`.
- **Idempotency**: one issue per day per edition (`--force` regenerates that edition only); each article is covered once; `published_at` / `sent_at` make job retries safe.
- **Newsletter inbox**: paid newsletters forwarded from Gmail to any address on `in.behindbars.news` land in S3 (SES receiving, eu-west-1) and become articles during `collect`. Tracking links are decoded offline and reduced to the clean post URL (they carry personal tokens); only text is stored. Forwarded newsletters are always bulletin candidates. On the public page their stories show title and link only; email and vault keep everything.
- **Story votes**: every story of the email (top stories and 'In breve') carries vote links: 'più così · meno così' under a top story, '+ −' at the end of a brief. Each link is signed with HMAC over `issue_date`, a hash of the story's first source URL and the vote. The link opens a page of the `serve` service whose script casts the vote, so link scanners that open mail links without running JS record nothing; a `<noscript>` button covers browsers without JS. The last click wins, the page offers 'Annulla'. Votes on issues older than 30 days are refused. The public page and the vault note carry no buttons.
- **Story essays**: Opus writes a short Italian piece (450-650 words) for each top story from the full text of every source in its cluster (120k characters per story, split evenly, at most 20k per source; sources without readable text go in as their summary). Shape: a two-sentence lede (what happened, why it matters), 3-4 sections with headings that say what they are about, short paragraphs, then a 'Cosa seguire' list. Outlets are named only for claims they alone make or where they disagree; disagreements on minor details are left out. The essay is stored in the issue (`TopStory.essay`) and served by `serve` at a signed `/e/` link under the story in the email. The page is private because essays may draw on paid newsletters, never expires, and is sent with `Referrer-Policy: no-referrer` so source sites never see the link. Its footer says how many sources were read in full. A refused or failed essay only drops the link.
- **Vault**: the bulletin and recap notes go to `Digests/`; the leads of the top stories (high tier, confidence ≥ 0.8) are appended to the Second Brain topic notes, once per URL.

## Stack

Python 3.13, uv, SQLAlchemy 2 async + asyncpg, Alembic, Postgres 15 (a database on a shared Cloud SQL instance), Gemini (`google-genai`, structured output), Anthropic SDK (Opus 5.5 with server-side refusal fallbacks), Jinja2, AWS SES (SMTP out, receiving in), S3 via boto3 with keyless Google → AWS web-identity federation, Google Cloud Storage, Terraform.

## Local development

```bash
uv sync
cp .env.example .env              # GEMINI_API_KEY, optional ANTHROPIC_API_KEY, DB_PASSWORD=feedbrain
docker compose up -d db           # Postgres 15 on localhost:5433
uv run feed-brain migrate
uv run feed-brain import-opml ~/Documents/Feeds.opml
uv run feed-brain collect         # fetch + triage
uv run feed-brain bulletin        # writes ./site/bulletins/<date>.html and .md
uv run feed-brain weekly --week 2026-W45
uv run feed-brain run --dry-run   # whole day into ./site and ./outbox, no email
```

Quality gates (also enforced before commits):

```bash
make check       # ruff, ruff format, ty, unit tests
make test-e2e    # Alembic + asyncpg integration tests on the docker Postgres
```

## Commands

| Command | What it does |
|---------|--------------|
| `run [--dry-run] [--to EMAIL]` | Scheduled entry point: migrate, collect, each edition (generate, deliver), weekly if due. Non-zero exit if any step failed (the others still run) |
| `collect [--dry-run]` | Sync feeds from the GCS OPML (when `DATA_BUCKET` is set), fetch, read the newsletter inbox (when `INBOX_BUCKET` is set; `--dry-run` keeps the mails), triage |
| `bulletin [--date] [--edition KEY] [--force] [--out]` | Generate (or reuse) the day's issue of one edition (default `daily`) and render it locally |
| `weekly [--week YYYY-Www] [--force] [--out]` | Generate the recap for a week (default: the week that is due) |
| `serve` | Service behind the email's vote and essay links (Cloud Run service; listens on `$PORT`) |
| `sync-vault [--outbox DIR] [--vault DIR] [--no-opml]` | Mac side of the vault bridge |
| `migrate` | Apply Alembic migrations |
| `analyze --article-id N` | Opus deep analysis of one article |
| `reextract [--hours 48] [--min-chars 1500]` | Extract again recent articles whose stored text is short (teasers, failed pages); longer text replaces the stored one |
| `import-opml FILE [--sync]`, `fetch`, `triage`, `list`, `reset`, `integrate` | Maintenance |

## Configuration

All settings come from environment variables (`.env` locally, job spec and Secret Manager in the cloud). The main ones:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMINI_API_KEY` | required | Triage, clustering, writing |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Gemini model |
| `ANTHROPIC_API_KEY` | unset | Enables Opus deep analysis of the top stories |
| `ANALYZER_MODEL` / `ANALYZER_EFFORT` | `claude-opus-5-5` / `high` | Deep analysis model and effort |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | local docker | Postgres |
| `SITE_BUCKET`, `DATA_BUCKET`, `SITE_URL` | unset | Public site, private outbox/OPML bucket, public base URL |
| `SES_USR`, `SES_PWD`, `SENDER_EMAIL`, `DIGEST_RECIPIENT` | unset | Email via SES SMTP |
| `TOP_STORIES`, `SECTION_CAP`, `CANDIDATE_WINDOW_HOURS` | 5, 3, 36 | Bulletin shape |
| `ANALYSIS_TOP_N`, `ANALYSIS_MIN_CHARS` | 3, 1500 | Opus runs on the first N leads with at least that much content |
| `VAULT_PATH`, `LOCAL_OPML_PATH` | iCloud vault, `~/Documents/Feeds.opml` | Mac sync only |
| `FETCH_PROXY` | unset | Proxy for sites that block cloud IPs (403/419/429 and network errors get one retry through it), e.g. `socks5://user:pass@host:1080` |
| `INBOX_BUCKET`, `INBOX_REGION` | unset, `eu-west-1` | Newsletter inbox bucket (no bucket = no inbox) |
| `INBOX_ROLE_ARN`, `INBOX_AUDIENCE` | unset, `feed-brain-inbox` | Cloud Run: AWS role assumed with the job's Google identity. Unset locally: the default AWS chain (`AWS_PROFILE`) |
| `INBOX_MAX_PER_RUN` | 50 | Mails read per run |
| `FEEDBACK_URL`, `FEEDBACK_KEY` | unset | Vote service base URL and the HMAC key that signs (job) and checks (service) the links; both unset = no buttons |
| `FEEDBACK_MAX_AGE_DAYS` | 30 | Votes on older issues are refused |

## Deployment runbook (GCP)

The stack lives in `infra/` with its own Terraform state. Shared BehindBarsPulse resources (Cloud SQL instance, VPC connector, SES and Gemini secrets) are referenced by name and never managed here.

1. **State bucket** (once):
   ```bash
   gcloud storage buckets create gs://playground-maroffo-feed-brain-tfstate \
     --project playground-maroffo --location europe-west1 --uniform-bucket-level-access
   gcloud storage buckets update gs://playground-maroffo-feed-brain-tfstate --versioning
   ```
2. **Variables**: `cp infra/terraform.tfvars.example infra/terraform.tfvars` and set `image` and `digest_recipient`.
3. **Image repository first, then the image**:
   ```bash
   cd infra && terraform init && terraform apply -target=google_artifact_registry_repository.images
   gcloud auth configure-docker europe-west1-docker.pkg.dev
   docker buildx build --platform linux/amd64 \
     -t europe-west1-docker.pkg.dev/playground-maroffo/feed-brain/feed-brain:$(git rev-parse --short HEAD) --push ..
   ```
4. **Everything else**: `terraform apply` (job, scheduler, buckets, DB + user, service accounts, IAM, the public vote service `feed-brain-feedback` and its link key; the job gets `FEEDBACK_URL` from it).
5. **Migrate and first run**:
   ```bash
   gcloud run jobs execute feed-brain --region europe-west1 --args=migrate --wait
   gcloud run jobs execute feed-brain --region europe-west1 --wait
   ```
   To regenerate and resend an issue: `--args=bulletin,--force,--out,/tmp/site` (add `--edition,KEY` for a thematic one), then a plain run delivers it. The image's `/app` is read-only: without `--out /tmp/site` the command stores the issue and then fails writing local files, and the job's retry regenerates it again.
6. **Opus deep analysis** (optional): create the secret, then set `anthropic_secret_id = "feed-brain-anthropic-api-key"` in `terraform.tfvars` and apply again.
   ```bash
   printf '%s' "$ANTHROPIC_API_KEY" | gcloud secrets create feed-brain-anthropic-api-key \
     --project playground-maroffo --replication-policy automatic --data-file=-
   ```
7. **Proxy for blocked feeds** (optional): some sites (Hacker News item pages, tumblr) refuse Google Cloud IPs. With NordVPN, take the *service credentials* (Nord Account → NordVPN → manual setup) and a SOCKS5 server, then:
   ```bash
   printf '%s' 'socks5://SERVICE_USER:SERVICE_PASS@amsterdam.nl.socks.nordhold.net:1080' | \
     gcloud secrets create feed-brain-fetch-proxy --project playground-maroffo --replication-policy automatic --data-file=-
   ```
   and set `fetch_proxy_secret_id = "feed-brain-fetch-proxy"` in `terraform.tfvars`, then apply.
8. **Newsletter inbox**: set `aws_profile` in `terraform.tfvars` to the AWS CLI profile of the account that owns the SES domain, then apply: it creates the S3 bucket, an SES receipt rule set active in `inbox_aws_region` (SES allows one active set per region: pick one no other workload uses), the IAM role the job assumes, and the `in.behindbars.news` MX record. In Gmail: Settings → Forwarding → add `newsletter@in.behindbars.news` (the confirmation mail lands in the bucket; collect leaves it there), then a filter per sender with "Forward to".
9. **Mac sync**: `gcloud auth application-default login`, put `DATA_BUCKET=<terraform output data_bucket>` in `.env`, then
   ```bash
   cp ops/launchd/it.maroffo.feed-brain.sync-vault.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/it.maroffo.feed-brain.sync-vault.plist
   ```
   Edit `~/Documents/Feeds.opml` as usual: the next sync uploads it and the next run picks it up.

New releases: build and push a new tag, update `image`, `terraform apply`. `run` applies pending migrations itself; when a release adds one the vote service needs before the next morning, run `--args=migrate` right after the apply.

## License

MIT
