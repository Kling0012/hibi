# hibi-server

The sync server for [Hibi](https://github.com/Kling0012/hibi), a desktop work journal. It collects activity records from your PCs and work records from AI agents, and provides the timeline, hybrid search, work-session detection, daily reports and the blog. The admin UI served at `/ui/` (for phone/tablet too) and the blog at `/blog/` also come from this server.

**Docs: https://kling0012.github.io/hibi/server.html**

| Repository | Role |
|---|---|
| [hibi](https://github.com/Kling0012/hibi) | Desktop app (Mac / Windows) |
| **hibi-server** (this repo) | Sync server |
| [hibi-memory](https://github.com/Kling0012/hibi-memory) | Shared AI memory |

## What it does

- Ingests records from all your devices and stores them per device, in time order
- Hybrid search combining semantic similarity (pgvector) and text matching (pg_trgm)
- Work-session declarations with AI-judged endings (the end time is the last related record's time)
- Periodic import of AI agent work from the shared memory ([hibi-memory](https://github.com/Kling0012/hibi-memory))
- Nightly daily reports and blog drafts (AI can also write via MCP)
- Admin UI at `/ui/`, blog at `/blog/`, MCP server at `/mcp`

Python 3.12 / FastAPI / PostgreSQL 17 + pgvector. Runs with Docker Compose.

> **There is no user authentication.** Bind only to `127.0.0.1` or an address on a closed network such as Tailscale. Never expose the API directly to the internet.

## Run it in 5 minutes (Docker Compose)

Requires Docker with the Compose plugin.

1. Get the code and copy the example config:

   ```sh
   git clone https://github.com/Kling0012/hibi-server.git
   cd hibi-server
   cp .env.example .env
   ```

2. Point it at an OpenAI-compatible LLM — the only required setting. Put the API key in a file and set the endpoint in `.env` (`LLM_BASE_URL`, and `LLM_MODEL` if your endpoint names models differently):

   ```sh
   mkdir -p secrets && chmod 700 secrets
   printf '%s\n' 'YOUR_LLM_API_KEY' > secrets/llm-key
   chmod 600 secrets/*
   ```

3. Start and check health:

   ```sh
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   curl -s http://127.0.0.1:18740/healthz   # {"ok":true,"db":"ok","embed_pending":0}
   ```

4. Open the admin UI: <http://127.0.0.1:18740/ui/>

DB migrations run automatically at startup. Embeddings are optional: with the default `EMBED_PROVIDER=fake` the server makes no external embedding calls and search works on full-text matching alone; see section 3 of `.env.example` to enable semantic search with Cloudflare Workers AI.

### Syncing from other devices

By default the server binds to `127.0.0.1` only. To sync from other machines, set `BIND_ADDR` in `.env` to the server's address on a closed network (for example a Tailscale tailnet IP such as `100.x.y.z`) and restart:

```sh
# .env
BIND_ADDR=100.x.y.z
```

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Then, in the Hibi desktop app on each machine, set the sync server to `http://<server-host>:18740` (the `MINECONTEXT_SYNC_URL` setting), e.g. `http://100.x.y.z:18740`.

## Security notes

- No endpoint requires authentication: anyone who can reach the port can read and write all data.
- Keep the server on `127.0.0.1` or a trusted closed network (Tailscale, a VPN, or a LAN you fully control). Do not publish the port to the internet — no `0.0.0.0` binding, no public IP, no port forwarding.
- Secrets (the LLM API key and, if used, the Cloudflare token) are file-mounted read-only from `secrets/`. Keep that directory out of anything you share.

## Configuration

Everything lives in `.env` — `.env.example` lists the items top-down (required first) with a short JP/EN comment on each: LLM endpoint, listen address/port, semantic search, AI work import, generated-post behavior, backup destination and tuning knobs.

Backups: `scripts/backup.sh` runs `pg_dump` into `BACKUP_DIR` and prunes dumps older than `KEEP_DAYS` days. Update and restore procedures: [docs](https://kling0012.github.io/hibi/server.html).

## Development

- API and data-model reference: [docs/reference.md](docs/reference.md)
- Try it without any external API: `docker compose up -d --build` (dev shape: `127.0.0.1:18741`, fake providers), UI at <http://127.0.0.1:18741/ui/>
- E2E tests: `sudo ./scripts/e2e.sh` (temporary compose project `hibi-e2e`, cleaned up afterwards)

## License

Apache License 2.0

## Development

This repository is developed by AI agents. Triaging issues and pull requests, reproducing and fixing bugs, review, tests, merges and production deploys are all handled automatically.
