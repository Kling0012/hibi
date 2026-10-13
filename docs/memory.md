---
title: AI の共有記憶
nav_order: 6
---

**日本語** · [English](en/memory.html) · [中文](zh/memory.html)

# AI の共有記憶（hibi-memory）
{: .no_toc }

Claude Code や Codex などの AI エージェントが、決めたこと・調べたこと・直したことを書き残し、別の AI や別の端末からも探せるようにする記憶の置き場です。Hibi はここに残った記録を「AI の作業」としてタイムラインに載せます。リポジトリ: [Kling0012/hibi-memory](https://github.com/Kling0012/hibi-memory)
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## 構成

| サービス | 中身 | 待ち受け |
|---|---|---|
| gateway | FastAPI + MCP。AI に見せる唯一の入口 | `8377`（REST、`/mcp/`、`/docs`） |
| valkey | 短期記憶（期限付きの作業メモ） | `127.0.0.1:16379` |
| falkordb | 長期記憶（Graphiti の時系列ナレッジグラフ） | `127.0.0.1:16380` |

AI は gateway の MCP か REST だけを使い、DB には直接触りません。

### 記憶が保存されるまで

1. AI が `memory_remember` を呼ぶと、gateway は元の記録をすぐ保存して `enrichment_status=pending` を返します（数十ミリ秒）。
2. 裏で 1 件ずつ、LLM で人物・物事・関係を抜き出し、グラフに加えます（数分かかることがあります）。
3. 補完の前でも、言葉が強く一致すれば検索に出ます。

## 起動

```sh
git clone https://github.com/Kling0012/hibi-memory.git
cd hibi-memory
cp .env.example .env   # LLM と埋め込みの接続先・鍵を書く
docker compose up -d --build
./scripts/verify.sh    # /health と /status を確認
```

### 主な設定（`.env`）

| 変数 | 説明 |
|---|---|
| `MEMORY_LLM_BASE_URL` / `MEMORY_LLM_API_KEY` / `MEMORY_LLM_MODEL` | 構造の抜き出しに使う OpenAI 互換 API（例: GLM-5.3-Flash） |
| `MEMORY_EMBEDDING_MODE` | `local`（手元の llama-server）/ `cloudflare` / `cloudflare_primary_local_fallback` |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | Cloudflare Workers AI を使うとき |
| `MEMORY_EMBEDDING_DIM` | 埋め込みの次元（既定 1024。Hibi の同期サーバと同じ Qwen3-Embedding-0.6B） |
| `MEMORY_DEFAULT_USER` / `MEMORY_DEFAULT_PROJECT` | 指定がないときの持ち主とプロジェクト |
| `MEMORY_SEARCH_RERANK_MODE` | `on` で検索結果を LLM で並べ替える（精度が上がり、遅くなる） |

`.env` は Git に入れないでください。

### 埋め込みの予備

Cloudflare が回数制限・時間切れ・接続失敗・5xx を返したときだけ、手元の llama-server（`Qwen3-Embedding-0.6B` の GGUF）に切り替え、その後 300 秒は手元を使い続けます。設定の誤り（400 / 401 / 403）では切り替えません。llama-server の systemd ユニットの例は `systemd/` にあります。

## AI エージェントからの使い方

### 登録

```sh
# Claude Code
claude mcp add --transport http hibi-memory http://<memory-host>:8377/mcp/
```

ほかのエージェントも、MCP の HTTP サーバとして `http://<memory-host>:8377/mcp/` を登録します。接続すると、使い方の方針が `memory://usage` として渡されます。

### MCP のツール

| ツール | 内容 |
|---|---|
| `memory_search` | 意味と言葉で探す |
| `memory_get` | 1 件を全文で読む（補完の状態も） |
| `memory_list` | 期間で一覧（作成順、カーソルで続きを取る） |
| `memory_remember` | 長く残す記録を保存 |
| `memory_supersede` | 古い記録を、履歴を残したまま新しい内容に置き換える |
| `session_put` / `session_get` / `session_search` | 作業中のメモ（既定 24 時間で消える） |
| `session_commit` | 作業中のメモのうち、確定したものを長期の記録にする |

削除のツール（`memory_forget`）もありますが、運用では使わず、置き換え（supersede）で直します。

### 書き残すとよいこと

- 利用者が決めたこと、好み、避けること
- 機器やサーバの構成、手順
- 原因がわかった不具合と直し方、試してだめだった方法
- 実験の条件と結果

鍵、パスワード、トークン、ファイルの全文、大量のログは書かないでください。

## REST API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/health` / `/status` | 状態と件数 |
| POST | `/v1/memories/search` | 検索 |
| GET | `/v1/memories` | 期間で一覧（`since` / `until` / `field` / `types` / `agent` / `limit`（最大 1000）/ `cursor`） |
| GET | `/v1/memories/{id}` | 1 件 |
| POST | `/v1/memories` | 保存 |
| POST | `/v1/memories/{id}/supersede` | 置き換え |
| POST | `/v1/sessions/put` / `/v1/sessions/commit` | 作業中のメモと確定 |

詳しくは `http://<memory-host>:8377/docs` の OpenAPI を見てください。

{: .note }
クエリに時刻を書くとき、`+09:00` の `+` は `%2B` にしてください。そのままだと空白として扱われ、0 件になります。

## Hibi との関係

同期サーバは `AM_BASE_URL` に書いた共有記憶から、`GET /v1/memories` で新しい記録を 5 分ごとに読み取ります（書き込みはしません）。

- 1 件の記憶が、タイムラインの「AI の作業」1 件になります。
- レーンは書いた AI ごと（例: `AI claude`、`AI codex`）に分かれます。
- 置き換えられた古い記録は、タイムラインで既定では表示しません。
- 正しい記録はいつも共有記憶の側にあります。直すときは共有記憶で置き換えてください。

## 注意

- 利用者認証はありません。閉じたネットワークの中だけで使ってください。
- 記録の ID を知っていれば、誰でも読み書きできます。
- `docker compose down -v` は記憶ごと消えるので使わないでください。
