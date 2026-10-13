---
title: セットアップ
nav_order: 2
---

**日本語** · [English](en/getting-started.html) · [中文](zh/getting-started.html)

# セットアップ
{: .no_toc }

同期サーバを立て、デスクトップアプリをつなぎ、必要なら AI の共有記憶もつなぐまでの手順です。
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## 用意するもの

| もの | 用途 | 備考 |
|---|---|---|
| 常時動いている Linux マシン 1 台 | 同期サーバ（と共有記憶） | 2 コア・2 GB で足ります。Docker Compose が必要 |
| Mac（Apple シリコン）または Windows | デスクトップアプリ | 複数台でも可。Intel Mac と Linux には対応しません |
| 閉じたネットワーク | 端末とサーバをつなぐ | [Tailscale](https://tailscale.com/) を推奨。サーバには認証がありません |
| 画面を読む AI の API キー | 画面の要約 | [Google AI Studio](https://aistudio.google.com/) の Gemini（無料枠あり）、または OpenAI 互換 API |
| 文章を書く AI の API キー | 作業終了の判定・日報・ブログ | OpenAI 互換 API。例: Zhipu の GLM-5.3 / GLM-5.3-Flash |
| 埋め込みの API | 検索 | Cloudflare Workers AI（無料枠あり）か OpenAI 互換 API。試すだけなら不要 |

{: .warning }
同期サーバと共有記憶には利用者認証がありません。必ず Tailscale の IP か `127.0.0.1` にだけ公開し、`0.0.0.0` やグローバル IP では待ち受けないでください。

## 1. 同期サーバを立てる

### まず動かしてみる（外部の API なし）

外部の API を使わない開発用の設定で起動できます。埋め込みと LLM は決定的なダミーになります。

```sh
git clone https://github.com/Kling0012/hibi-server.git
cd hibi-server
docker compose up -d --build
curl -s http://127.0.0.1:18741/healthz
# {"ok":true,"db":"ok","embed_pending":0}
```

ブラウザで `http://127.0.0.1:18741/ui/` を開くと管理画面が出ます。

### 本番として動かす

`.env.example` を `.env` にコピーして、待ち受け先と AI の接続先を書きます。

```sh
cp .env.example .env
mkdir -p secrets && chmod 700 secrets
```

`.env` の主な項目:

| 変数 | 例 | 意味 |
|---|---|---|
| `PROD_TAILNET_IP` | `100.x.y.z` | 待ち受ける Tailscale の IP（`tailscale ip -4` で確認） |
| `PROD_PORT` | `18740` | 待ち受けるポート |
| `EMBED_PROVIDER` | `cloudflare` | 埋め込み。`cloudflare` / `openai_compat` / `fake` |
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/coding/paas/v4` | 作業終了の判定・日報・ブログに使う OpenAI 互換 API |
| `AM_BASE_URL` | `http://<memory-host>:8377` | AI の共有記憶。使わないなら空 |

鍵はファイルに置きます（値を `.env` や compose に直接書かない）。

```sh
# 文章を書く AI の API キー（1 行）
printf '%s\n' 'YOUR_LLM_KEY' > secrets/llm-key
# Cloudflare Workers AI を使う場合
cat > secrets/cloudflare.env <<'ENV'
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
ENV
chmod 600 secrets/*
```

起動:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p hibi up -d --build
curl -s http://$PROD_TAILNET_IP:18740/healthz
```

DB の作成と移行は起動時に自動で行われます。詳しい設定は [同期サーバ](server.md) を見てください。

{: .important }
埋め込みの次元（`EMBED_DIM`、既定 1024）は最初の起動で DB に固定されます。あとから変えるには DB を作り直す必要があるので、最初に決めてください。

## 2. デスクトップアプリをつなぐ

### インストール

[Releases](https://github.com/Kling0012/hibi/releases) から OS に合ったものを入れます。

| OS | ファイル | 初回の注意 |
|---|---|---|
| macOS（Apple シリコン） | `Hibi-<版>.dmg` | 署名していないため、入れたあと一度だけ `xattr -dr com.apple.quarantine /Applications/Hibi.app` を実行 |
| Windows | `Hibi-<版>-setup.exe` | SmartScreen が出たら「詳細情報」→「実行」 |

macOS では、初回の記録開始時に「画面収録」の許可を求められます。

### 初回の設定

初めて起動すると「Hibi へようこそ」の設定画面が開きます。

1. **AI** タブで、画面を読む AI と文章を書く AI の接続先を入れます。
   - Gemini: 種類 `gemini_live`、モデル `gemini-3.8-live`。キーはファイル（例: `~/.config/minecontext/gemini-key`）か直接入力。
   - GLM など OpenAI 互換: 種類 `openai_compat`、ベース URL とモデル名、キー。
   - 「接続テスト」で届くか確認できます。
2. **同期** タブで「同期サーバへ送る」をオンにし、サーバの URL（例: `http://100.x.y.z:18740`）と端末名を入れて保存します。
3. **はじめる** を押すとホーム画面に移ります。

ホームの「今日」に数字が出て、タイムラインが表示されればつながっています。

### 記録を始める

サイドバーの **画面の記録** →「記録の対象」で画面を選び、「記録を開始」を押します。記録したくないアプリは **設定 → 画面の記録** の「記録しないアプリ」に書いておきます。

## 3. AI の共有記憶をつなぐ（任意）

Claude Code や Codex の作業をタイムラインに載せたい場合だけ必要です。

1. [hibi-memory](https://github.com/Kling0012/hibi-memory) を同じネットワーク内のサーバで起動します（[AI の共有記憶](memory.md)）。
2. AI エージェントに MCP サーバとして登録します。

   ```sh
   claude mcp add --transport http hibi-memory http://<memory-host>:8377/mcp/
   ```

3. 同期サーバの `.env` に `AM_BASE_URL=http://<memory-host>:8377` を書いて再起動します。5 分ごとに新しい記録が取り込まれます。管理画面の **状態** タブの「今すぐ取り込む」で手動でも取り込めます。

## 4. スマホ・iPad から見る

同じ Tailscale ネットワークに入った端末のブラウザで `http://<server>:18740/ui/` を開きます。ホーム画面に追加すると、アプリのように使えます。ブログは `http://<server>:18740/blog/` です。
