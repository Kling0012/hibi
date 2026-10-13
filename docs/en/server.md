---
title: Sync server
---

[日本語](../server.html) · **English** · [中文](../zh/server.html)

# Sync server (hibi-server)
{: .no_toc }

The server that collects records from all devices and AI work, and handles the timeline, search, work-end detection, daily reports, and blog. Repository: [Kling0012/hibi-server](https://github.com/Kling0012/hibi-server)
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## Composition

| Piece | Contents |
|---|---|
| API | Python 3.12, FastAPI, SQLAlchemy 2, psycopg 3, Alembic |
| DB | PostgreSQL 17 + pgvector + pg_trgm (`pgvector/pgvector:pg17`) |
| UI | `/ui/` (management), `/blog/` (blog). `/` redirects to `/ui/` |
| Startup | Docker Compose (`db` and `api`). On startup, `api` migrates the DB to the latest schema automatically |

A small machine suffices (production limits: API 512 MB, DB 768 MB).

### Background jobs

| Job | Interval | What it does |
|---|---|---|
| Embedding | 5 s (10 s in production) | Embeds records that do not have embeddings yet, in batches |
| Work-end detection | 120 s (300 s in production) | Asks the LLM whether in-progress work has finished |
| AI work import | 300 s | Imports new records from the shared memory |
| Daily report | Daily 23:55 / next 6:00 | Writes the day's report and rewrites it the next morning |
| Blog | Right after the daily report | Drafts posts depending on configuration ([Daily reports and blog](blog.html)) |

On failure, each job simply skips that run; record intake and search keep running.

## Startup

### Development (without external APIs)

```sh
docker compose up -d --build
curl -s http://127.0.0.1:18741/healthz
```

Embeddings and the LLM become deterministic dummies (`fake`) that talk to nothing external.

### Production

Layer `docker-compose.prod.yml` on top. Configuration goes in `.env`, keys in `secrets/` ([Setup](getting-started.html#run-it-in-production)).

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p hibi up -d --build
```

What the production layer changes:

- Listen only on `PROD_TAILNET_IP:PROD_PORT` and `127.0.0.1:PROD_PORT`
- Embeddings via Cloudflare Workers AI (`@cf/qwen/qwen3-embedding-0.6b`, 1024 dimensions)
- LLM pointed at `LLM_BASE_URL` from `.env`
- Keys from `secrets/` mounted read-only into the container
- Memory limits and automatic restart (`restart: unless-stopped`)

{: .warning }
The server has no user authentication. Do not listen on `0.0.0.0` or a public IP.

## Environment variables

### Embeddings

| Variable | Default | Description |
|---|---|---|
| `EMBED_PROVIDER` | `fake` | `fake` / `openai_compat` / `cloudflare` |
| `EMBED_BASE_URL` | — | Base URL for `openai_compat` (up to `.../v1`) |
| `EMBED_MODEL` | `fake-embed` | Model name. For cloudflare, `@cf/qwen/qwen3-embedding-0.6b` |
| `EMBED_DIM` | `1024` | Dimensions. **Fixed in the DB on first startup**. Changing it requires rebuilding the DB |
| `EMBED_API_KEY_FILE` | — | Key file for `openai_compat` |
| `EMBED_CF_ENV_FILE` | — | File containing `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` |
| `EMBED_BATCH` | `64` | Batch size per run (Workers AI rejects large batches, so 32 in production) |
| `EMBED_INTERVAL_SEC` | `5` | Interval in seconds |
| `EMBED_RELAY_MAX_INPUTS` | `64` | Max inputs accepted per call by the embedding relay (`POST /v1/embeddings`) |

### LLM (work-end detection, daily report, blog)

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai_compat` | `openai_compat` / `fake` |
| `LLM_BASE_URL` | — | OpenAI-compatible base URL. If empty, detection / reports / blog are disabled |
| `LLM_API_KEY_FILE` | — | Key file |
| `LLM_MODEL` | `glm-5.3-flash` | Model used for work-end detection |
| `LLM_TIMEOUT_SEC` | `30` | Timeout in seconds |
| `SESSION_JUDGE_INTERVAL` | `120` | Detection interval in seconds |
| `SESSION_END_THRESHOLD` | `0.75` | Confidence at or above which the work counts as "finished" |
| `IDLE_MINUTES` | `15` | Minutes without records on any device before ending work |
| `REPORT_LLM_MODEL` | `glm-5.3` | Model that writes daily reports |
| `REPORT_LLM_FLASH_MODEL` | `glm-5.3-flash` | Model used to pre-summarize long material |
| `REPORT_LLM_TIMEOUT_SEC` | `180` | Daily report timeout in seconds |
| `REPORT_CHUNK_CHARS` | `12000` | Material longer than this is pre-summarized in 4-hour chunks before writing |
| `REPORT_AUTO_ENABLED` | `1` | Automatic daily report generation |
| `BLOG_AUTO` | `draft` | Automatic blog writing. `off` / `draft` / `publish` ([Daily reports and blog](blog.html)) |

### AI work import

| Variable | Default | Description |
|---|---|---|
| `AM_BASE_URL` | — | Shared memory URL (e.g. `http://<memory-host>:8377`). If empty, no import |
| `AM_IMPORT_ENABLED` | `1` | Enable / disable the import |
| `AM_IMPORT_INTERVAL` | `300` | Interval in seconds |
| `AM_IMPORT_LOOKBACK_HOURS` | `48` | Hours to look back on the first run |
| `AM_TIMEOUT_SEC` | `15` | Timeout in seconds |

### Others

| Variable | Default | Description |
|---|---|---|
| `TZ_DEFAULT` | `Asia/Tokyo` | Timezone used for day boundaries |
| `API_PORT` | `18741` | Development listen port |
| `PROD_TAILNET_IP` / `PROD_PORT` | — / `18740` | Production listen address |
| `MCP_ALLOWED_HOSTS` | — | Hosts accepted by MCP (`/mcp`), e.g. `100.x.y.z:18740`, comma-separated. If empty, the Host is not checked |

## Data

| Table | Contents |
|---|---|
| `devices` | Devices (name, OS, last seen) |
| `events` | Records. Kinds are `activity` / `screen_summary` / `note` / `todo` / `ai_work` / `report`. IDs are device-generated UUIDs; the same ID overwrites |
| `event_embeddings` | Embeddings of records. Records without one queue for embedding |
| `work_sessions` | Declared work. Only one in progress at a time. End reasons: `manual` / `ai_detected` / `idle` / `superseded` |
| `blog_posts` | Blog posts |
| `importer_state` | Import progress |

Timestamps are accepted with a timezone and returned in UTC.

## How search works

`POST /v1/search` combines two searches:

1. Top 50 by embedding-vector closeness (pgvector, cosine distance)
2. Top 50 by string similarity (pg_trgm)

The two are merged by rank (Reciprocal Rank Fusion, k=60) and returned with scores and evidence. Records that have not been embedded yet can still be found by the second search.

## Operations

### Updating

```sh
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p hibi up -d --build api
```

UI files are baked into the image, so UI-only changes still need `--build`.

### Backups

`scripts/backup.sh` saves the DB to `backups/` with `pg_dump -Fc` and deletes anything older than 14 days. Run it daily via a systemd timer or cron.

```sh
# Inspect contents
docker exec -i hibi-db-1 pg_restore -l < backups/hibi-YYYYMMDD-HHMM.dump
```

Restore (note that this rebuilds the DB):

```sh
docker compose -p hibi stop api
docker exec -i hibi-db-1 psql -U sync -d postgres -c 'DROP DATABASE sync' -c 'CREATE DATABASE sync OWNER sync'
docker exec -i hibi-db-1 pg_restore -U sync --no-owner -d sync < backups/hibi-YYYYMMDD-HHMM.dump
docker compose -p hibi start api
```

Do not use `docker compose down -v`; it deletes the DB volume as well.

### Tests

```sh
sudo ./scripts/e2e.sh
```

Starts a temporary stack under a different project name, exercises the API with dummy embeddings and LLM, then tears it down.

## Notes

- If the container cannot reach the shared memory, check whether the host firewall (ufw etc.) blocks traffic from containers.
- The development DB username and password are fixed values. The DB port is not exposed externally.
- Work that the AI finished stays unconfirmed. Confirm or fix it in the management UI.
