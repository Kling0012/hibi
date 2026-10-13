---
title: Sync server API
---

[日本語](../api.html) · **English** · [中文](../zh/api.html)

# Sync server API
{: .no_toc }

A reference for the sync server's REST API and MCP. There is no authentication. Timestamps are passed as ISO 8601 with timezone; responses are UTC (`Z`). The full OpenAPI reference is at `http://<server>:18740/docs` on a running server.
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## Status

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | `{ok, db, embed_pending}`. `embed_pending` is the number of records awaiting embedding |

## Devices and records

| Method | Path | Description |
|---|---|---|
| PUT | `/v1/devices/{device_id}` | Register / update a device (`name`, `os`) |
| POST | `/v1/events:batch` | Send records in a batch (max 500). The same `id` overwrites → `{accepted, updated}` |
| GET | `/v1/events` | List records (`from` / `device_id` / `kind` / `limit` / `cursor`; oldest first) |
| GET | `/v1/days/{day}` | All devices' records for a day plus per-kind counts (`tz`) |
| DELETE | `/v1/events/{event_id}` | Delete one record |

Record shape:

```json
{
  "id": "device-generated UUID",
  "device_id": "device ID",
  "kind": "activity | screen_summary | note | todo | ai_work | report",
  "started_at": "2026-10-13T09:00:00+09:00",
  "ended_at": null,
  "app": "Code", "title": "window title",
  "text": "body",
  "meta": {}
}
```

## Search

| Method | Path | Description |
|---|---|---|
| POST | `/v1/search` | Hybrid search |

```json
{"query": "database migration", "kinds": ["screen_summary", "ai_work"],
 "from": "2026-10-01T00:00:00+09:00", "to": null, "device_ids": null, "k": 30}
```

Vector closeness (top 50) and string similarity (top 50) are merged by rank (RRF, k=60) and returned with scores and evidence.

## Embedding relay

| Method | Path | Description |
|---|---|---|
| POST | `/v1/embeddings` | OpenAI-compatible. `{model, input}` → vectors. PCs can use the server's embeddings without holding keys |

`model` is ignored and the server's `EMBED_MODEL` is used. Inputs beyond `EMBED_RELAY_MAX_INPUTS` (default 64) return 422; embedding failures return 502.

## Work sessions

| Method | Path | Description |
|---|---|---|
| POST | `/v1/sessions` | Declare (`title`, `note?`, `device_id?`). Ends any in-progress work → `{session, superseded}` |
| GET | `/v1/sessions/active` | In-progress work |
| GET | `/v1/sessions` | List |
| GET | `/v1/sessions/{id}` | One |
| POST | `/v1/sessions/{id}/end` | End (`ended_at?`). Becomes manually confirmed |
| PATCH | `/v1/sessions/{id}` | Fix / confirm (`title` / `note` / `started_at` / `ended_at` / `confirmed`) |
| POST | `/v1/sessions/{id}/cancel` | Cancel |

End reasons (`end_reason`): `manual`, `ai_detected` (AI decision), `idle`, `superseded` (superseded by the next declaration). Those ended by AI or idleness have `confirmed=false`.

## Timeline and stats

| Method | Path | Description |
|---|---|---|
| GET | `/v1/timeline` | One day (`date`, `tz`, `include_superseded?`). Merges work, records, and AI work into a single time-ordered stream with total time per work |
| GET | `/v1/stats/daily` | Daily stats (`from`, `to`, `tz`; up to 400 days. Days without records come back as 0) |

One day of `/v1/stats/daily`:

```json
{"date": "2026-10-13", "events": 54, "mine": 53, "ai_work": 1,
 "active_minutes": 20, "ai_active_minutes": 5, "session_minutes": 0,
 "first_at": "...", "last_at": "..."}
```

`active_minutes` is the number of 5-minute slots with records × 5.

## Daily reports

| Method | Path | Description |
|---|---|---|
| POST | `/v1/reports/daily` | Write (`date`, `tz`, `force?`). If one already exists, it is returned unless `force` is set |
| GET | `/v1/reports/daily` | Read (`date`) |
| GET | `/v1/reports` | List (`from`, `to`) |

## Blog

| Method | Path | Description |
|---|---|---|
| GET | `/v1/blog/posts` | List (newest first; `type` / `tag` / `q` / `status` / `from` / `to` / `limit` / `cursor`). Defaults to published only. `status` accepts `draft` / `published` / `archived` / `all` |
| GET | `/v1/blog/posts/{slug}` | Full text with neighboring posts |
| GET | `/v1/blog/tags` | Counts per tag (published) |
| GET | `/v1/blog/materials` | Writing material for a day (`date`, `tz`, `chars?`, `ai_chars?`, `max_records?`) |
| POST | `/v1/blog/posts` | Create (`title`, `markdown`, `date`, `type`, `tags?`, `author`, `status`) |
| PATCH | `/v1/blog/posts/{slug}` | Update (only the given fields) |
| POST | `/v1/blog/posts/{slug}/publish` | Publish |
| POST | `/v1/blog/posts/{slug}/unpublish` | Back to draft |
| DELETE | `/v1/blog/posts/{slug}` | Put away (sets `archived`, does not delete) |
| POST | `/v1/blog/posts/{slug}/rewrite` | LLM rewrite proposal (`instruction?`). Does not save |
| POST | `/v1/blog/generate` | Have the LLM write (`date`, `tz`, `force?`, `types?`). Destination follows `BLOG_AUTO` |

## AI work import

| Method | Path | Description |
|---|---|---|
| GET | `/v1/importers` | Import status (cursor, last success, error, counts) |
| POST | `/v1/importers/agent-memory/run` | Import now → `{ok, fetched, upserted, pages, cursor_ts}` |

## MCP

`http://<server>:18740/mcp` (streamable HTTP).

| Tool | Description |
|---|---|
| `hibi_get_day_timeline` | Timeline of the day |
| `hibi_search_records` | Search records |
| `hibi_get_blog_materials` | Blog material |
| `hibi_list_posts` / `hibi_get_post` | Read posts |
| `hibi_create_draft` / `hibi_update_post` | Create / fix drafts |
| `hibi_publish_post` / `hibi_unpublish_post` | Publish / return to draft |

See [Daily reports and blog](blog.html#having-ai-write-blog-posts) for usage.
