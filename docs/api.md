---
title: 同期サーバ API
nav_order: 8
---

**日本語** · [English](en/api.html) · [中文](zh/api.html)

# 同期サーバ API
{: .no_toc }

同期サーバの REST API と MCP の一覧です。認証はありません。時刻はタイムゾーン付き ISO 8601 で渡し、応答は UTC（`Z`）で返ります。動いているサーバの `http://<server>:18740/docs` で、全項目の OpenAPI を見られます。
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## 状態

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/healthz` | `{ok, db, embed_pending}`。`embed_pending` は埋め込み待ちの件数 |

## 端末と記録

| メソッド | パス | 内容 |
|---|---|---|
| PUT | `/v1/devices/{device_id}` | 端末の登録・更新（`name`, `os`） |
| POST | `/v1/events:batch` | 記録をまとめて送る（最大 500 件）。同じ `id` は上書き → `{accepted, updated}` |
| GET | `/v1/events` | 記録の一覧（`from` / `device_id` / `kind` / `limit` / `cursor`。古い順） |
| GET | `/v1/days/{day}` | ある日の全端末の記録と、種類ごとの件数（`tz`） |
| DELETE | `/v1/events/{event_id}` | 記録を 1 件消す |

記録の形:

```json
{
  "id": "端末が作る UUID",
  "device_id": "端末 ID",
  "kind": "activity | screen_summary | note | todo | ai_work | report",
  "started_at": "2026-10-13T09:00:00+09:00",
  "ended_at": null,
  "app": "Code", "title": "ウィンドウ名",
  "text": "本文",
  "meta": {}
}
```

## 検索

| メソッド | パス | 内容 |
|---|---|---|
| POST | `/v1/search` | ハイブリッド検索 |

```json
{"query": "データベースの移行", "kinds": ["screen_summary", "ai_work"],
 "from": "2026-10-01T00:00:00+09:00", "to": null, "device_ids": null, "k": 30}
```

ベクトルの近さ（上位 50）と文字の似かた（上位 50）を順位で混ぜ（RRF、k=60）、点数と根拠付きで返します。

## 埋め込みの中継

| メソッド | パス | 内容 |
|---|---|---|
| POST | `/v1/embeddings` | OpenAI 互換。`{model, input}` → ベクトル。PC は鍵を持たずにサーバの埋め込みを使える |

`model` は無視され、サーバの `EMBED_MODEL` を使います。入力が `EMBED_RELAY_MAX_INPUTS`（既定 64）を超えると 422、埋め込みの失敗は 502 です。

## 作業（work sessions）

| メソッド | パス | 内容 |
|---|---|---|
| POST | `/v1/sessions` | 宣言（`title`, `note?`, `device_id?`）。進行中の作業があれば終わらせる → `{session, superseded}` |
| GET | `/v1/sessions/active` | 進行中の作業 |
| GET | `/v1/sessions` | 一覧 |
| GET | `/v1/sessions/{id}` | 1 件 |
| POST | `/v1/sessions/{id}/end` | 終える（`ended_at?`）。手動で確定済みになる |
| PATCH | `/v1/sessions/{id}` | 直す・確定する（`title` / `note` / `started_at` / `ended_at` / `confirmed`） |
| POST | `/v1/sessions/{id}/cancel` | 取り消す |

終わり方（`end_reason`）: `manual`（手動）、`ai_detected`（AI の判断）、`idle`（放置）、`away`（長い離席）、`superseded`（次の作業の宣言）。手動以外で終わったものは `confirmed=false` です。

`duration_minutes` は離席を引いた時間で、引いた分は `away_minutes` に入ります。

## ToDo

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/v1/todos` | 一覧（`status=open|done|all`, `limit?`）→ `{todos: [...]}`。open は急ぎの順・新しい順 |
| POST | `/v1/todos` | 追加（`title`, `note?`, `urgency?`(`high`/`medium`/`low`), `due_date?`, `evidence?`, `device_id?`）。ToDo の自動作成はしないので、`source` は `me` だけを受け付ける |
| PATCH | `/v1/todos/{id}` | 直す・完了にする（`title` / `note` / `urgency` / `due_date` / `status`(`open`/`done`)） |
| DELETE | `/v1/todos/{id}` | 削除（`archived` にする） |
| GET | `/v1/todos/{id}/entries` | 計上した時間の一覧 |
| DELETE | `/v1/todos/{id}/entries/{entry_id}` | 計上した時間を取り消す |

ToDo には `tracked_minutes`（足された時間の合計）、`last_tracked_at`、`tracking`（いま取り組んでいるか）が付きます。

## 離席

端末は離席から戻ったとき、`kind=away` の記録を 1 件送ります（`started_at`, `ended_at`, `meta.reason` = `idle` / `lock` / `suspend`）。ほかの端末でその間に記録があれば、その分は離席として扱いません。離席は作業時間・ToDo の時間・`active_minutes` から除かれます。

## タイムラインと集計

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/v1/timeline` | 1 日分（`date`, `tz`, `include_superseded?`）。作業・記録・AI の作業を時刻順に 1 本にし、作業ごとの合計時間を付ける。ToDo の計上は `todo_entries` に入る |
| GET | `/v1/stats/daily` | 日ごとの集計（`from`, `to`, `tz`。最大 400 日。記録のない日も 0 で返す） |

`/v1/stats/daily` の 1 日分:

```json
{"date": "2026-10-13", "events": 54, "mine": 53, "ai_work": 1,
 "active_minutes": 20, "ai_active_minutes": 5, "session_minutes": 0, "away_minutes": 0,
 "first_at": "...", "last_at": "..."}
```

`active_minutes` は記録のあった 5 分枠の数 × 5 です。

## 日報

| メソッド | パス | 内容 |
|---|---|---|
| POST | `/v1/reports/daily` | 書く（`date`, `tz`, `force?`）。すでにあれば `force` がない限りそれを返す |
| GET | `/v1/reports/daily` | 読む（`date`） |
| GET | `/v1/reports` | 一覧（`from`, `to`） |

## ブログ

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/v1/blog/posts` | 一覧（新しい順。`type` / `tag` / `q` / `status` / `from` / `to` / `limit` / `cursor`）。既定は公開済みだけ。`status` に `draft` / `published` / `archived` / `all` |
| GET | `/v1/blog/posts/{slug}` | 全文と前後の記事 |
| GET | `/v1/blog/tags` | タグごとの件数（公開済み） |
| GET | `/v1/blog/materials` | その日の執筆材料（`date`, `tz`, `chars?`, `ai_chars?`, `max_records?`） |
| POST | `/v1/blog/posts` | 作る（`title`, `markdown`, `date`, `type`, `tags?`, `author`, `status`） |
| PATCH | `/v1/blog/posts/{slug}` | 直す（指定した項目だけ） |
| POST | `/v1/blog/posts/{slug}/publish` | 公開する |
| POST | `/v1/blog/posts/{slug}/unpublish` | 下書きに戻す |
| DELETE | `/v1/blog/posts/{slug}` | しまう（`archived` にするだけで消さない） |
| POST | `/v1/blog/posts/{slug}/rewrite` | LLM の書き直し案（`instruction?`）。保存はしない |
| POST | `/v1/blog/generate` | LLM に書かせる（`date`, `tz`, `force?`, `types?`）。行き先は `BLOG_AUTO` に従う |

## AI の作業の取り込み

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/v1/importers` | 取り込みの状態（カーソル、最後の成功、エラー、件数） |
| POST | `/v1/importers/agent-memory/run` | 今すぐ取り込む → `{ok, fetched, upserted, pages, cursor_ts}` |

## MCP

`http://<server>:18740/mcp`（streamable HTTP）。

| ツール | 内容 |
|---|---|
| `hibi_get_day_timeline` | その日のタイムライン |
| `hibi_search_records` | 記録の検索 |
| `hibi_get_blog_materials` | ブログの材料 |
| `hibi_list_posts` / `hibi_get_post` | 記事を読む |
| `hibi_create_draft` / `hibi_update_post` | 下書きを作る・直す |
| `hibi_publish_post` / `hibi_unpublish_post` | 公開する・下書きに戻す |
| `hibi_list_todos` / `hibi_add_todo` / `hibi_complete_todo` | ToDo を読む・足す・完了にする |

使い方は [日報とブログ](blog.md#ai-にブログを書かせる) を見てください。
