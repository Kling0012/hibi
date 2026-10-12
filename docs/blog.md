---
title: 日報とブログ
nav_order: 7
---

# 日報とブログ
{: .no_toc }

1. TOC
{:toc}

## 日報とブログの違い

| | 日報 | ブログ |
|---|---|---|
| 目的 | その日の事実を漏れなく残す | 人に読ませる文章として残す |
| 書き手 | 同期サーバの LLM | 同期サーバの LLM、外部の AI、自分 |
| 公開 | すぐ読める | 下書き → 確認 → 公開 |
| 場所 | アプリの **日報**、`/ui/#report` | アプリの **ブログ**、`/blog/` |

## 日報

毎日 23:55 にその日の分を書き、翌朝 6:00 に書き直します。材料は、宣言した作業、画面の要約と活動、AI の作業です。

- 6 つの節で書きます（今日の概要、作業ごとの時間、自分がしたこと、AI がしたこと、自分と AI の作業の関係、明日に持ち越すこと）。
- 文ごとに根拠の時刻（例:「（14:05）」）を付けます。
- 材料が長い日は、4 時間ごとに軽いモデルで下まとめしてから書きます。
- アプリの日報画面の「今すぐ作成」で、いつでも書き直せます。

## ブログ

### 種類

| 種類 | 内容 |
|---|---|
| 1日のまとめ（summary） | 1 日 1 本。一人称の「です・ます」で、取り組んだこと、つまずき、判断、学び、次にやることを物語として書く。1500〜3000 字 |
| 技術的な学び（learning） | 1 日に最大 3 本。同じ問題に当たった人（未来の自分）向けに、症状、原因、対処を順に書く。最後に根拠の時刻と ID |
| 記事（post） | 自由な記事。外部の AI や自分が書く |

どの種類も、材料にない事実は書かない決まりです。保存の前に、鍵やトークンに見える文字列（`Bearer` の値、`password=` の値、32 文字以上の鍵らしい文字列）を伏せ字にします。

### 自動で書く設定

同期サーバの `BLOG_AUTO` で、日報のあとに LLM が書いた記事をどうするか決めます。

| 値 | 動き |
|---|---|
| `off` | 自動では書かない |
| `draft`（既定） | 下書きに入れる。自分で確認して公開する |
| `publish` | すぐ公開する |

### 記事の状態

| 状態 | 意味 |
|---|---|
| `draft` | 下書き。ブログの一覧には出ない |
| `published` | 公開。ブログの一覧に出る |
| `archived` | しまったもの。消しはしない |

書き手（`author`）は、`auto`（同期サーバの LLM）、`me`（自分）、`agent:<名前>`（外部の AI）のどれかです。

## AI にブログを書かせる

Claude Code などの AI に、その日の記録を読ませて記事を書かせられます。同期サーバは MCP サーバとしても動いています。

### 登録

```sh
claude mcp add --transport http hibi http://<server>:18740/mcp
```

### 頼み方の例

> Hibi の今日の材料を読んで、ブログの下書きを書いて。題材は同期サーバの検索を作り直した話で。

AI は次の順に進めます。

1. `hibi_get_blog_materials` と `hibi_get_day_timeline` で材料を集める
2. `hibi_create_draft` で下書きを作る
3. 自分が `/blog/` やアプリで読み、直してほしいところを伝える → `hibi_update_post`
4. よければ `hibi_publish_post` で公開する

{: .important }
AI に書かせた記事は必ず下書きにし、自分で読んでから公開してください。MCP のツールの説明にもこの決まりが書いてあります。

### MCP のツール

| ツール | 種類 | 内容 |
|---|---|---|
| `hibi_get_day_timeline(date)` | 読む | その日のタイムライン |
| `hibi_search_records(query, from_time?, to_time?, kinds?)` | 読む | 記録の検索 |
| `hibi_get_blog_materials(date)` | 読む | その日の材料（作業と時間、自分の記録、AI の作業、日報） |
| `hibi_list_posts(status?, type?, limit?)` | 読む | 記事の一覧 |
| `hibi_get_post(slug)` | 読む | 記事の全文 |
| `hibi_create_draft(title, markdown, date, type?, tags?, author_name)` | 書く | 下書きを作る |
| `hibi_update_post(slug, ...)` | 書く | 記事を直す |
| `hibi_publish_post(slug)` | 書く | 公開する |
| `hibi_unpublish_post(slug)` | 書く | 下書きに戻す |

### REST で書く

MCP を使わない場合は REST でも同じことができます（[同期サーバ API](api.md#ブログ)）。

```sh
B=http://<server>:18740
# 材料
curl -s "$B/v1/blog/materials?date=2026-10-13"
# 下書きを作る
curl -s -X POST $B/v1/blog/posts -H 'Content-Type: application/json' -d '{
  "title": "検索を作り直した", "markdown": "## きっかけ\n\n...",
  "date": "2026-10-13", "type": "post", "tags": ["hibi"],
  "author": "agent:claude", "status": "draft"}'
# 公開する
curl -s -X POST $B/v1/blog/posts/<slug>/publish
```

同期サーバの LLM に書き直し案を出させることもできます（`POST /v1/blog/posts/<slug>/rewrite`。保存はしないので、よければ `PATCH` で保存します）。
