---
title: AI shared memory
parent: English
nav_order: 6
---

[日本語](../memory.html) · **English** · [中文](../zh/memory.html)

# AI shared memory (hibi-memory)
{: .no_toc }

A memory store where AI agents such as Claude Code and Codex leave behind decisions, findings, and fixes, so that other AIs and other devices can find them later. Hibi puts the records left here on the timeline as "AI work". Repository: [Kling0012/hibi-memory](https://github.com/Kling0012/hibi-memory)
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## Composition

| Service | Contents | Listens on |
|---|---|---|
| gateway | FastAPI + MCP. The only entry point exposed to AIs | `8377` (REST, `/mcp/`, `/docs`) |
| valkey | Short-term memory (expiring work notes) | `127.0.0.1:16379` |
| falkordb | Long-term memory (Graphiti's temporal knowledge graph) | `127.0.0.1:16380` |

AIs use only the gateway's MCP or REST interface; they never touch the DB directly.

### How a memory gets stored

1. When an AI calls `memory_remember`, the gateway stores the raw record immediately and returns `enrichment_status=pending` (tens of milliseconds).
2. In the background, one record at a time, an LLM extracts people, things, and relations and adds them to the graph (this can take minutes).
3. Even before enrichment, records with strong textual matches appear in search.

## Startup

```sh
git clone https://github.com/Kling0012/hibi-memory.git
cd hibi-memory
cp .env.example .env   # fill in LLM and embedding endpoints and keys
docker compose up -d --build
./scripts/verify.sh    # checks /health and /status
```

### Main settings (`.env`)

| Variable | Description |
|---|---|
| `MEMORY_LLM_BASE_URL` / `MEMORY_LLM_API_KEY` / `MEMORY_LLM_MODEL` | OpenAI-compatible API used for structure extraction (e.g. GLM-5.3-Flash) |
| `MEMORY_EMBEDDING_MODE` | `local` (local llama-server) / `cloudflare` / `cloudflare_primary_local_fallback` |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | When using Cloudflare Workers AI |
| `MEMORY_EMBEDDING_DIM` | Embedding dimensions (default 1024; the same Qwen3-Embedding-0.6B as Hibi's sync server) |
| `MEMORY_DEFAULT_USER` / `MEMORY_DEFAULT_PROJECT` | Owner and project when unspecified |
| `MEMORY_SEARCH_RERANK_MODE` | `on` reranks search results with an LLM (better accuracy, slower) |

Do not commit `.env` to Git.

### Embedding fallback

Only when Cloudflare returns a rate limit, timeout, connection failure, or 5xx does the system switch to a local llama-server (GGUF of `Qwen3-Embedding-0.6B`) and keep using it for the next 300 seconds. Configuration errors (400 / 401 / 403) do not trigger a switch. An example systemd unit for llama-server is in `systemd/`.

## Using it from AI agents

### Registration

```sh
# Claude Code
claude mcp add --transport http hibi-memory http://<memory-host>:8377/mcp/
```

Other agents register `http://<memory-host>:8377/mcp/` as an MCP HTTP server as well. On connection, usage guidelines are provided as `memory://usage`.

### MCP tools

| Tool | What it does |
|---|---|
| `memory_search` | Search by meaning and wording |
| `memory_get` | Read one record in full (including enrichment status) |
| `memory_list` | List by period (creation order, cursor for more) |
| `memory_remember` | Save a record meant to last |
| `memory_supersede` | Replace an old record with new content, keeping the history |
| `session_put` / `session_get` / `session_search` | Work-in-progress notes (expire after 24 hours by default) |
| `session_commit` | Promote the settled part of work-in-progress notes into long-term memory |

A deletion tool (`memory_forget`) exists too, but operations use supersede to correct records instead.

### What is worth writing down

- Decisions the user made, preferences, things to avoid
- Machine and server layouts, procedures
- Bugs with a known cause and their fixes, approaches that were tried and failed
- Conditions and results of experiments

Never write keys, passwords, tokens, full file contents, or bulk logs.

## REST API

| Method | Path | What it does |
|---|---|---|
| GET | `/health` / `/status` | Status and counts |
| POST | `/v1/memories/search` | Search |
| GET | `/v1/memories` | List by period (`since` / `until` / `field` / `types` / `agent` / `limit` (max 1000) / `cursor`) |
| GET | `/v1/memories/{id}` | One record |
| POST | `/v1/memories` | Save |
| POST | `/v1/memories/{id}/supersede` | Replace |
| POST | `/v1/sessions/put` / `/v1/sessions/commit` | Work-in-progress notes and promotion |

See the OpenAPI docs at `http://<memory-host>:8377/docs` for details.

{: .note }
When writing timestamps in a query, encode the `+` of `+09:00` as `%2B`. Left as-is, it is treated as a space and yields zero results.

## Relationship with Hibi

The sync server reads new records from the shared memory configured in `AM_BASE_URL` via `GET /v1/memories` every 5 minutes (it never writes).

- One memory becomes one "AI work" entry on the timeline.
- Lanes are split per writing AI (e.g. `AI claude`, `AI codex`).
- Old, superseded records are hidden from the timeline by default.
- The authoritative record always lives in the shared memory. To fix something, supersede it there.

## Notes

- There is no user authentication. Use it only inside a closed network.
- Anyone who knows a record's ID can read and write it.
- `docker compose down -v` deletes the memories too — do not use it.
