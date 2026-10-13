---
title: 同期サーバ
nav_order: 5
---

**日本語** · [English](en/server.html) · [中文](zh/server.html)

# 同期サーバ（hibi-server）
{: .no_toc }

全端末の記録と AI の作業を集め、タイムライン・検索・作業終了の判定・日報・ブログを受け持つサーバです。リポジトリ: [Kling0012/hibi-server](https://github.com/Kling0012/hibi-server)
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## 構成

| もの | 中身 |
|---|---|
| API | Python 3.12、FastAPI、SQLAlchemy 2、psycopg 3、Alembic |
| DB | PostgreSQL 17 + pgvector + pg_trgm（`pgvector/pgvector:pg17`） |
| 画面 | `/ui/`（管理画面）、`/blog/`（ブログ）。`/` は `/ui/` へ |
| 起動 | Docker Compose（`db` と `api`）。`api` の起動時に DB を自動で最新の形へ移行 |

小さなマシンで十分です（本番の上限は API 512 MB、DB 768 MB）。

### バックグラウンドの仕事

| 仕事 | 周期 | 内容 |
|---|---|---|
| 埋め込み | 5 秒（本番 10 秒） | まだ埋め込みのない記録をまとめて埋め込む |
| 作業終了の判定 | 120 秒（本番 300 秒） | 進行中の作業が終わったかを LLM に判断させる。30 分以上の離席でも終える |
| ToDo の時間の計上 | 300 秒 | 新しい記録を見て、どの ToDo に取り組んでいたかを LLM に判断させ、時間を足す |
| AI の作業の取り込み | 300 秒 | 共有記憶から新しい記録を取り込む |
| 日報 | 毎日 23:55 / 翌 6:00 | その日の日報を書き、翌朝に書き直す |
| ブログ | 日報の直後 | 設定に応じて記事の下書きを作る（[日報とブログ](blog.md)） |

どれも失敗したらその回を飛ばすだけで、記録の受け付けや検索は止まりません。

## 起動

### 開発用（外部の API なし）

```sh
docker compose up -d --build
curl -s http://127.0.0.1:18741/healthz
```

埋め込みと LLM は外部に通信しない決定的なダミー（`fake`）になります。

### 本番

`docker-compose.prod.yml` を重ねて起動します。設定は `.env`、鍵は `secrets/` に置きます（[セットアップ](getting-started.md#本番として動かす)）。

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p hibi up -d --build
```

本番の重ね合わせで変わること:

- 待ち受けを `PROD_TAILNET_IP:PROD_PORT` と `127.0.0.1:PROD_PORT` だけにする
- 埋め込みを Cloudflare Workers AI（`@cf/qwen/qwen3-embedding-0.6b`、1024 次元）にする
- LLM を `.env` の `LLM_BASE_URL` に向ける
- `secrets/` の鍵を読み取り専用でコンテナに渡す
- メモリの上限と自動再起動（`restart: unless-stopped`）

{: .warning }
サーバに利用者認証はありません。`0.0.0.0` やグローバル IP で待ち受けないでください。

## 環境変数

### 埋め込み

| 変数 | 既定 | 説明 |
|---|---|---|
| `EMBED_PROVIDER` | `fake` | `fake` / `openai_compat` / `cloudflare` |
| `EMBED_BASE_URL` | — | `openai_compat` のベース URL（`.../v1` まで） |
| `EMBED_MODEL` | `fake-embed` | モデル名。cloudflare は `@cf/qwen/qwen3-embedding-0.6b` |
| `EMBED_DIM` | `1024` | 次元。**最初の起動で DB に固定**。変えるには DB の作り直しが必要 |
| `EMBED_API_KEY_FILE` | — | `openai_compat` のキーのファイル |
| `EMBED_CF_ENV_FILE` | — | `CLOUDFLARE_ACCOUNT_ID` と `CLOUDFLARE_API_TOKEN` を書いたファイル |
| `EMBED_BATCH` | `64` | 1 回にまとめる件数（Workers AI は大きいと断るので本番は 32） |
| `EMBED_INTERVAL_SEC` | `5` | 周期（秒） |
| `EMBED_RELAY_MAX_INPUTS` | `64` | 埋め込みの中継（`POST /v1/embeddings`）が 1 回に受ける件数 |

### LLM（作業終了の判定・日報・ブログ）

| 変数 | 既定 | 説明 |
|---|---|---|
| `LLM_PROVIDER` | `openai_compat` | `openai_compat` / `fake` |
| `LLM_BASE_URL` | — | OpenAI 互換のベース URL。空なら判定・日報・ブログを行わない |
| `LLM_API_KEY_FILE` | — | キーのファイル |
| `LLM_MODEL` | `glm-5.3-flash` | 作業終了の判定に使うモデル |
| `LLM_TIMEOUT_SEC` | `30` | 時間切れ（秒） |
| `SESSION_JUDGE_INTERVAL` | `120` | 判定の周期（秒） |
| `SESSION_END_THRESHOLD` | `0.75` | この確信度以上で「終わった」とする |
| `IDLE_MINUTES` | `15` | 全端末で記録がこの分数なければ終わりにする |
| `REPORT_LLM_MODEL` | `glm-5.3` | 日報を書くモデル |
| `REPORT_LLM_FLASH_MODEL` | `glm-5.3-flash` | 材料が長いときの下まとめに使うモデル |
| `REPORT_LLM_TIMEOUT_SEC` | `180` | 日報の時間切れ（秒） |
| `REPORT_CHUNK_CHARS` | `12000` | 材料がこれを超えたら 4 時間ごとに下まとめしてから書く |
| `REPORT_AUTO_ENABLED` | `1` | 日報の自動作成 |
| `BLOG_AUTO` | `draft` | ブログの自動作成。`off` / `draft` / `publish`（[日報とブログ](blog.md)） |

### AI の作業の取り込み

| 変数 | 既定 | 説明 |
|---|---|---|
| `AM_BASE_URL` | — | 共有記憶の URL（例: `http://<memory-host>:8377`）。空なら取り込まない |
| `AM_IMPORT_ENABLED` | `1` | 取り込みの有効・無効 |
| `AM_IMPORT_INTERVAL` | `300` | 周期（秒） |
| `AM_IMPORT_LOOKBACK_HOURS` | `48` | 初回にさかのぼる時間 |
| `AM_TIMEOUT_SEC` | `15` | 時間切れ（秒） |

### その他

| 変数 | 既定 | 説明 |
|---|---|---|
| `TZ_DEFAULT` | `Asia/Tokyo` | 日付の区切りに使うタイムゾーン |
| `API_PORT` | `18741` | 開発用の待ち受けポート |
| `PROD_TAILNET_IP` / `PROD_PORT` | — / `18740` | 本番の待ち受け |
| `SESSION_AWAY_END_MINUTES` | `30` | 作業中にこの分数以上離席したら、離席した時刻で作業を終える |
| `TODO_JUDGE_INTERVAL` | `300` | ToDo の時間の計上を動かす間隔（秒） |
| `TODO_MATCH_THRESHOLD` | `0.7` | ToDo に取り組んでいたと判断する確信度の下限 |
| `TODO_JUDGE_MAX_WINDOW_MIN` | `30` | 1 回に見る記録の時間幅（分） |
| `TODO_MERGE_GAP_MIN` | `5` | 前の計上からこの分数以内なら、同じ帯を延ばす |
| `MCP_ALLOWED_HOSTS` | — | MCP（`/mcp`）で受け付ける Host（例: `100.x.y.z:18740`、カンマ区切り）。空なら Host を確かめない |

## データ

| テーブル | 中身 |
|---|---|
| `devices` | 端末（名前、OS、最後に見えた時刻） |
| `events` | 記録。種類は `activity` / `screen_summary` / `note` / `todo` / `ai_work` / `report`。ID は端末が作る UUID で、同じ ID は上書き |
| `event_embeddings` | 記録の埋め込み。ない記録が埋め込みの待ち行列 |
| `work_sessions` | 宣言した作業。進行中は同時に 1 件だけ。終わり方は `manual` / `ai_detected` / `idle` / `superseded` |
| `blog_posts` | ブログ記事 |
| `importer_state` | 取り込みの進み具合 |

時刻はタイムゾーン付きで受け取り、UTC で返します。

## 検索のしくみ

`POST /v1/search` は 2 つの検索を合わせます。

1. 埋め込みのベクトルが近いもの上位 50 件（pgvector、コサイン距離）
2. 文字の並びが似ているもの上位 50 件（pg_trgm）

両者を順位で混ぜ（Reciprocal Rank Fusion、k=60）、点数と根拠を付けて返します。埋め込みがまだの記録も 2 で見つかります。

## 運用

### 更新

```sh
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p hibi up -d --build api
```

画面のファイルはイメージに入るので、画面だけの変更でも `--build` が必要です。

### バックアップ

`scripts/backup.sh` は `pg_dump -Fc` で DB を `backups/` に保存し、14 日より古いものを消します。systemd の timer か cron で毎日動かしてください。

```sh
# 中身の確認
docker exec -i hibi-db-1 pg_restore -l < backups/hibi-YYYYMMDD-HHMM.dump
```

復元（DB を作り直すので注意）:

```sh
docker compose -p hibi stop api
docker exec -i hibi-db-1 psql -U sync -d postgres -c 'DROP DATABASE sync' -c 'CREATE DATABASE sync OWNER sync'
docker exec -i hibi-db-1 pg_restore -U sync --no-owner -d sync < backups/hibi-YYYYMMDD-HHMM.dump
docker compose -p hibi start api
```

`docker compose down -v` は DB のボリュームごと消すので使わないでください。

### テスト

```sh
sudo ./scripts/e2e.sh
```

別のプロジェクト名で一時的に起動し、ダミーの埋め込み・LLM で API を一通り確かめてから片付けます。

## 注意

- コンテナから共有記憶へ届かないときは、ホストのファイアウォール（ufw など）がコンテナからの通信を止めていないか確認してください。
- 開発用の DB の利用者名とパスワードは固定値です。DB のポートは外へ出していません。
- AI が終わらせた作業は未確定のまま残ります。管理画面で確定・修正してください。
