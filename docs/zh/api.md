---
title: 同步服务器 API
parent: 中文
nav_order: 8
---

[日本語](../api.html) · [English](../en/api.html) · **中文**

# 同步服务器 API
{: .no_toc }

同步服务器 REST API 与 MCP 的一览。没有认证。时刻以带时区的 ISO 8601 传入，响应以 UTC（`Z`）返回。运行中服务器的 `http://<server>:18740/docs` 可查看全部条目的 OpenAPI。
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## 状态

| 方法 | 路径 | 内容 |
|---|---|---|
| GET | `/healthz` | `{ok, db, embed_pending}`。`embed_pending` 是等待嵌入的条数 |

## 设备与记录

| 方法 | 路径 | 内容 |
|---|---|---|
| PUT | `/v1/devices/{device_id}` | 注册/更新设备（`name`、`os`） |
| POST | `/v1/events:batch` | 批量发送记录（最多 500 条）。相同 `id` 会覆盖 → `{accepted, updated}` |
| GET | `/v1/events` | 记录列表（`from` / `device_id` / `kind` / `limit` / `cursor`。按时间正序） |
| GET | `/v1/days/{day}` | 某天全部设备的记录及各类型条数（`tz`） |
| DELETE | `/v1/events/{event_id}` | 删除一条记录 |

记录的结构:

```json
{
  "id": "设备生成的 UUID",
  "device_id": "设备 ID",
  "kind": "activity | screen_summary | note | todo | ai_work | report",
  "started_at": "2026-10-13T09:00:00+09:00",
  "ended_at": null,
  "app": "Code", "title": "窗口标题",
  "text": "正文",
  "meta": {}
}
```

## 搜索

| 方法 | 路径 | 内容 |
|---|---|---|
| POST | `/v1/search` | 混合搜索 |

```json
{"query": "数据库迁移", "kinds": ["screen_summary", "ai_work"],
 "from": "2026-10-01T00:00:00+09:00", "to": null, "device_ids": null, "k": 30}
```

向量相近（前 50）与文字相似（前 50）按名次融合（RRF，k=60），附带分数和依据返回。

## 嵌入中继

| 方法 | 路径 | 内容 |
|---|---|---|
| POST | `/v1/embeddings` | OpenAI 兼容。`{model, input}` → 向量。电脑无需持有密钥即可使用服务器的嵌入 |

`model` 会被忽略，使用服务器的 `EMBED_MODEL`。输入超过 `EMBED_RELAY_MAX_INPUTS`（默认 64）返回 422，嵌入失败返回 502。

## 工作（work sessions）

| 方法 | 路径 | 内容 |
|---|---|---|
| POST | `/v1/sessions` | 声明（`title`、`note?`、`device_id?`）。有进行中的工作则先结束 → `{session, superseded}` |
| GET | `/v1/sessions/active` | 进行中的工作 |
| GET | `/v1/sessions` | 列表 |
| GET | `/v1/sessions/{id}` | 一条 |
| POST | `/v1/sessions/{id}/end` | 结束（`ended_at?`）。变为手动确认 |
| PATCH | `/v1/sessions/{id}` | 修改/确认（`title` / `note` / `started_at` / `ended_at` / `confirmed`） |
| POST | `/v1/sessions/{id}/cancel` | 取消 |

结束方式（`end_reason`）: `manual`（手动）、`ai_detected`（AI 判定）、`idle`（闲置）、`away`（长时间离开）、`superseded`（被下一项工作的声明取代）。以手动以外的方式结束的为 `confirmed=false`。

`duration_minutes` 是扣除离开时间后的时长，扣除部分放在 `away_minutes` 中。

## 待办

| 方法 | 路径 | 内容 |
|---|---|---|
| GET | `/v1/todos` | 列表（`status=open\|done\|all`、`limit?`）→ `{todos: [...]}`。open 按紧急程度、从新到旧排序 |
| POST | `/v1/todos` | 添加（`title`、`note?`、`urgency?`（`high`/`medium`/`low`）、`due_date?`、`source?`（`me`/`auto`）、`evidence?`、`device_id?`）。`auto` 遇到相似待办时不会新建，而是给已有条目加上 `deduped: true` 返回 |
| PATCH | `/v1/todos/{id}` | 修改/标记完成（`title` / `note` / `urgency` / `due_date` / `status`（`open`/`done`）） |
| DELETE | `/v1/todos/{id}` | 删除（设为 `archived`） |
| GET | `/v1/todos/{id}/entries` | 已计入时间的列表 |
| DELETE | `/v1/todos/{id}/entries/{entry_id}` | 撤销已计入的时间 |

待办包含 `tracked_minutes`（累加时间的总和）、`last_tracked_at`、`tracking`（当前是否正在处理）。

## 离开

设备在你回来后发送一条 `kind=away` 记录（`started_at`、`ended_at`、`meta.reason` = `idle` / `lock` / `suspend`）。如果这段时间其他设备有记录，相应部分就不算离开。离开的时间会从工作时间、待办时间、`active_minutes` 中排除。

## 时间线与统计

| 方法 | 路径 | 内容 |
|---|---|---|
| GET | `/v1/timeline` | 一天（`date`、`tz`、`include_superseded?`）。把工作、记录、AI 的工作按时间排成一条，并附每项工作的总时长。待办计入的时间放在 `todo_entries` 中 |
| GET | `/v1/stats/daily` | 按天统计（`from`、`to`、`tz`。最多 400 天。没有记录的日子也按 0 返回） |

`/v1/stats/daily` 的一天:

```json
{"date": "2026-10-13", "events": 54, "mine": 53, "ai_work": 1,
 "active_minutes": 20, "ai_active_minutes": 5, "session_minutes": 0, "away_minutes": 0,
 "first_at": "...", "last_at": "..."}
```

`active_minutes` 是有记录的 5 分钟格数 × 5。

## 日报

| 方法 | 路径 | 内容 |
|---|---|---|
| POST | `/v1/reports/daily` | 撰写（`date`、`tz`、`force?`）。已存在时除非 `force` 否则返回已有内容 |
| GET | `/v1/reports/daily` | 读取（`date`） |
| GET | `/v1/reports` | 列表（`from`、`to`） |

## 博客

| 方法 | 路径 | 内容 |
|---|---|---|
| GET | `/v1/blog/posts` | 列表（按新到旧。`type` / `tag` / `q` / `status` / `from` / `to` / `limit` / `cursor`）。默认只有已发布。`status` 可取 `draft` / `published` / `archived` / `all` |
| GET | `/v1/blog/posts/{slug}` | 全文及前后文章 |
| GET | `/v1/blog/tags` | 各标签条数（已发布） |
| GET | `/v1/blog/materials` | 当天的写作材料（`date`、`tz`、`chars?`、`ai_chars?`、`max_records?`） |
| POST | `/v1/blog/posts` | 创建（`title`、`markdown`、`date`、`type`、`tags?`、`author`、`status`） |
| PATCH | `/v1/blog/posts/{slug}` | 修改（只改指定项目） |
| POST | `/v1/blog/posts/{slug}/publish` | 发布 |
| POST | `/v1/blog/posts/{slug}/unpublish` | 退回草稿 |
| DELETE | `/v1/blog/posts/{slug}` | 归档（只置为 `archived`，不删除） |
| POST | `/v1/blog/posts/{slug}/rewrite` | LLM 的改写方案（`instruction?`）。不保存 |
| POST | `/v1/blog/generate` | 让 LLM 撰写（`date`、`tz`、`force?`、`types?`）。去向遵循 `BLOG_AUTO` |

## AI 工作导入

| 方法 | 路径 | 内容 |
|---|---|---|
| GET | `/v1/importers` | 导入状态（游标、最后成功、错误、条数） |
| POST | `/v1/importers/agent-memory/run` | 立即导入 → `{ok, fetched, upserted, pages, cursor_ts}` |

## MCP

`http://<server>:18740/mcp`（streamable HTTP）。

| 工具 | 内容 |
|---|---|
| `hibi_get_day_timeline` | 当天的时间线 |
| `hibi_search_records` | 搜索记录 |
| `hibi_get_blog_materials` | 博客材料 |
| `hibi_list_posts` / `hibi_get_post` | 阅读文章 |
| `hibi_create_draft` / `hibi_update_post` | 创建/修改草稿 |
| `hibi_publish_post` / `hibi_unpublish_post` | 发布/退回草稿 |
| `hibi_list_todos` / `hibi_add_todo` / `hibi_complete_todo` | 读取/添加/完成待办 |

用法请看[日报与博客](blog.html#让-ai-写博客)。
