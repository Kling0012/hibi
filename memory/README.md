# hibi-memory

Shared memory for AI agents. Agents leave what they learn — decisions,
configurations, procedures, fixes, experiment results — in one shared
collection, and search it back from any device or session. The gateway is
exposed as an [MCP](https://modelcontextprotocol.io/) server plus a REST API;
short-term session memory lives in Valkey and long-term memory in a
Graphiti-powered FalkorDB knowledge graph.

> **Part of Hibi.** This repository (hibi-memory) provides the "AI shared
> memory" of [Hibi](https://github.com/Kling0012/hibi), which keeps a single
> timeline of everything you and your AI agents worked on.
> Docs: https://kling0012.github.io/hibi/memory.html /
> Sync server: [hibi-server](https://github.com/Kling0012/hibi/tree/main/server)

## How it works

- `gateway/` — FastAPI + MCP Python SDK gateway. Scope validation, promotion
  rules and history management live here.
- `schemas/v1/` — Pydantic ontology, relationships and metadata types.
- `migration/` — import of durable records from the legacy SQLite memory.
- `evaluation/` — synthetic datasets and benchmark/verification scripts.
- Docker Compose runs three services: the gateway, Valkey 8.1 (short-term
  memory and locks) and FalkorDB (long-term memory).

The canonical `MemoryRecord` nodes and the temporal knowledge graph that
Graphiti extracts are stored in the same FalkorDB instance. This keeps
Graphiti's hybrid retrieval while preserving memory IDs, scopes, sources,
validity windows, schema versions and supersede chains without loss.

## Requirements

- Docker with the compose plugin, on a **Linux host** (the gateway uses
  `network_mode: host`; on macOS/Windows replace it with port mappings).
- An **OpenAI-compatible LLM API** (base URL + key). Graphiti's structured
  extraction calls it for every write; it will not start usefully without one.
- An embedding backend, one of:
  - `local` — an OpenAI-compatible embedding server on the host
    (default `http://127.0.0.1:8081/v1`, e.g. `llama-server`, see below);
  - `cloudflare` — Cloudflare Workers AI (account ID + API token).
- Optional: a TypeSafe Jev API for entity resolution (`MEMORY_DECISION_BACKEND`
  falls back to the LLM when unset, so this is not required).

## Quick start

```sh
cp .env.example .env
# edit .env: set MEMORY_LLM_BASE_URL, MEMORY_LLM_API_KEY, MEMORY_LLM_MODEL
# and pick an embedding mode
docker compose up -d --build
./scripts/verify.sh   # curls /health and /status
```

By default the gateway binds to `127.0.0.1:8377`.

- MCP endpoint: `http://<your-server>:8377/mcp/`
- REST OpenAPI docs: `http://<your-server>:8377/docs`

Agents connecting from other machines must pass DNS-rebinding protection:
set `MCP_ALLOWED_HOSTS` (comma-separated host names or IPs, e.g.
`MCP_ALLOWED_HOSTS=memory.lan,10.0.0.5`) in `.env` to the names the agents
use to reach the server. `localhost` and `127.0.0.1` are always allowed.

Note that `docker-compose.yml` pins `MEMORY_EMBEDDING_MODE` for the gateway
container unless your `.env` sets it; a plain copy of `.env.example` runs the
`local` embedding mode, which requires the embedding server from below.

## Configuration

Everything is configured through environment variables (`.env`, see
`.env.example` for the full list with comments). The important ones:

| Variable | Meaning |
|---|---|
| `MEMORY_DEFAULT_USER` / `MEMORY_DEFAULT_PROJECT` / `MEMORY_DEFAULT_AGENT` | Default scope for MCP tool calls (`default` / `all` / agent name) |
| `MCP_ALLOWED_HOSTS` | Extra hosts the MCP endpoint accepts (beyond localhost) |
| `MEMORY_LLM_BASE_URL` / `MEMORY_LLM_API_KEY` / `MEMORY_LLM_MODEL` | OpenAI-compatible extraction LLM |
| `MEMORY_EMBEDDING_MODE` | `local`, `cloudflare`, or `cloudflare_primary_local_fallback` |
| `MEMORY_INGESTION_MODE` | `graphiti` (default, synchronous), `graphiti_async` (durable accept + background enrichment), `typesafe_async` (experimental) |
| `MEMORY_NODE_RESOLUTION_PROVIDER` | `llm` or `jev` (entity resolution; `jev` falls back to the LLM on errors or missing keys) |

## MCP tools

- Long-term: `memory_search`, `memory_get`, `memory_list`, `memory_remember`,
  `memory_supersede`, `memory_forget`
- Short-term: `session_get`, `session_put`, `session_search`, `session_commit`

`session_put` accepts a typed long-term `candidate`; `session_commit` promotes
selected candidates. Observations stay short-term and are never auto-promoted.
Read the `memory://usage` resource after connecting: it documents the shared
operating policy all agents are expected to follow.

## Scopes

A search returns the union of:

```text
global
user/{user}
project/{project}/shared
project/{project}/{agent}
agent/{agent}/private
```

A common convention is one shared collection (`user`/`project` defaults) for
all devices and SSH targets, with real host and project names kept in the
content and tags rather than in separate scopes. The `agent_private` match on
agent names is self-declared, not authentication. There is no
multi-tenant authorization; run this on a trusted network (see below).

## Importing from the Hibi sync server

If you previously ran the older SQLite-based shared memory of the Hibi sync
server, `migration/import_legacy_sqlite.py` imports only the durable kinds
(`fact`, `decision`, `state`) over REST; `log` and `handoff` entries are
deliberately not migrated into the long-term graph.

```sh
python migration/import_legacy_sqlite.py /path/to/memory.db \
    --base-url http://127.0.0.1:8377 --dry-run
python migration/import_legacy_sqlite.py /path/to/memory.db \
    --base-url http://127.0.0.1:8377 \
    --user default --project all --manifest import-manifest.jsonl
```

Pass the target user explicitly (`--user`). The JSONL manifest is a checkpoint:
an interrupted import never silently retries a POST. Keep the legacy database
as a read-only backup; it is never modified.

## Local embedding server

For `MEMORY_EMBEDDING_MODE=local` (or the cloudflare-with-local-fallback mode),
run an OpenAI-compatible embedding server on the host, e.g. with llama.cpp:

```sh
llama-server -hf Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0 \
    --alias qwen3-embedding-0.6b --host 0.0.0.0 --port 8081 \
    --api-key local-embedding --embedding --pooling last --ctx-size 2048 --threads 2
```

The model (a few hundred MB, Apache-2.0) is fetched from Hugging Face on first
start. Add device flags such as `--device Vulkan1 --n-gpu-layers auto` or
`--device CUDA0` depending on your hardware; on CPU-only machines the default
works.

## Security notes

**There is no authentication.** Anyone who can reach the gateway can read,
write and delete the shared memory (deletion is policy-prohibited but not
enforced by auth). The gateway binds to `127.0.0.1` by default and enables
MCP DNS-rebinding protection with a localhost allowlist; widen it only for
hosts on a network you fully trust.

- Use this only inside a closed network (localhost, a VPN like Tailscale, or a
  private LAN). Never expose the port to the public internet.
- Do not store credentials, private keys, full file contents or bulk logs in
  the memory (the `memory://usage` policy says the same).

## Dependencies and licenses

Everything below is fetched at run/build time; nothing is bundled in this
repository. This project is Apache-2.0 (see `LICENSE`).

| Dependency | How it arrives | License |
|---|---|---|
| FalkorDB v4.20.3 | `falkordb/falkordb` image via docker compose | SSPLv1 (not OSI-approved; fine to run, review terms before offering it as a service) |
| Valkey 8.1 | `valkey/valkey` image via docker compose | BSD-3 |
| graphiti-core 0.30.2 | pip (`pyproject.toml`) | Apache-2.0 |
| fastapi / uvicorn / pydantic / redis / mcp | pip | MIT / BSD-3 |
| Qwen3-Embedding-0.6B (GGUF) | Hugging Face, on first start of `llama-server` | Apache-2.0 |
| OpenAI-compatible LLM API | you provide (`.env`) | your provider's terms |
| Cloudflare Workers AI embedding | optional (`.env`) | your provider's terms |
| TypeSafe Jev API | optional (`.env`) | your provider's terms |

## Development

```sh
python -m pip install -e '.[dev]'
pytest
```

Design documents:

- [Multi-agent shared memory architecture](docs/multi_agent_shared_memory_architecture.md)
- [Architecture addendum (decision engine)](docs/multi_agent_shared_memory_architecture_addendum.md)
- [Graphiti standard registration + Jev entity resolution](docs/graphiti_jev_integration.md)
- [Decision engine evaluation](evaluation/decision_engine_evaluation.md)

The gateway tests live in `gateway/tests/`; `evaluation/` contains synthetic
datasets (`evaluation/datasets/`) and the benchmark/verification scripts used
to evaluate the search and decision backends.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
