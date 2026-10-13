---
title: Setup
parent: English
nav_order: 2
---

[日本語](../getting-started.html) · **English** · [中文](../zh/getting-started.html)

# Setup
{: .no_toc }

This guide walks you through setting up the sync server, connecting the desktop app, and optionally connecting the AI shared memory.
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## What you need

| Item | Used for | Notes |
|---|---|---|
| One always-on Linux machine | Sync server (and shared memory) | 2 cores / 2 GB is enough. Docker Compose required |
| Mac (Apple silicon) or Windows | Desktop app | Multiple machines are fine. Intel Macs and Linux are not supported |
| A closed network | Connecting devices and the server | [Tailscale](https://tailscale.com/) recommended. The server has no authentication |
| API key for a vision AI | Screen summarization | Gemini from [Google AI Studio](https://aistudio.google.com/) (free tier available), or an OpenAI-compatible API |
| API key for a writing AI | Work-end detection, daily reports, blog | OpenAI-compatible API. Example: Zhipu's GLM-5.3 / GLM-5.3-Flash |
| Embedding API | Search | Cloudflare Workers AI (free tier available) or an OpenAI-compatible API. Not needed just to try it |

{: .warning }
The sync server and the shared memory have no user authentication. Expose them only on a Tailscale IP or `127.0.0.1`, and never listen on `0.0.0.0` or a public IP.

## 1. Set up the sync server

### Try it first (without external APIs)

You can start it with a development configuration that uses no external APIs. Embeddings and the LLM become deterministic dummies.

```sh
git clone https://github.com/Kling0012/hibi-server.git
cd hibi-server
docker compose up -d --build
curl -s http://127.0.0.1:18741/healthz
# {"ok":true,"db":"ok","embed_pending":0}
```

Open `http://127.0.0.1:18741/ui/` in a browser to see the management UI.

### Run it in production

Copy `.env.example` to `.env` and fill in the listen address and the AI endpoints.

```sh
cp .env.example .env
mkdir -p secrets && chmod 700 secrets
```

Main entries in `.env`:

| Variable | Example | Meaning |
|---|---|---|
| `PROD_TAILNET_IP` | `100.x.y.z` | Tailscale IP to listen on (check with `tailscale ip -4`) |
| `PROD_PORT` | `18740` | Port to listen on |
| `EMBED_PROVIDER` | `cloudflare` | Embeddings. `cloudflare` / `openai_compat` / `fake` |
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/coding/paas/v4` | OpenAI-compatible API used for work-end detection, daily reports, and blog |
| `AM_BASE_URL` | `http://<memory-host>:8377` | AI shared memory. Leave empty if not used |

Put keys in files (do not write values directly into `.env` or the compose file).

```sh
# API key of the writing AI (one line)
printf '%s\n' 'YOUR_LLM_KEY' > secrets/llm-key
# When using Cloudflare Workers AI
cat > secrets/cloudflare.env <<'ENV'
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
ENV
chmod 600 secrets/*
```

Start:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p hibi up -d --build
curl -s http://$PROD_TAILNET_IP:18740/healthz
```

Database creation and migration run automatically at startup. See [Sync server](server.html) for the full configuration.

{: .important }
The embedding dimension (`EMBED_DIM`, default 1024) is fixed in the database on first startup. Changing it later requires rebuilding the database, so decide it up front.

## 2. Connect the desktop app

### Install

Get the build for your OS from [Releases](https://github.com/Kling0012/hibi/releases).

| OS | File | First-run notes |
|---|---|---|
| macOS (Apple silicon) | `Hibi-<version>.dmg` | Unsigned; run `xattr -dr com.apple.quarantine /Applications/Hibi.app` once after installing |
| Windows | `Hibi-<version>-setup.exe` | If SmartScreen appears, click "More info" → "Run anyway" |

On macOS, the app asks for the "Screen Recording" permission the first time you start recording.

### Initial configuration

On first launch, the "Welcome to Hibi" setup screen opens.

1. In the **AI** tab, enter the endpoints of the vision AI and the writing AI.
   - Gemini: type `gemini_live`, model `gemini-3.8-live`. Key from a file (e.g. `~/.config/minecontext/gemini-key`) or entered directly.
   - GLM and other OpenAI-compatible: type `openai_compat`, base URL, model name, and key.
   - "Test connection" checks that it is reachable.
2. In the **Sync** tab, turn on "Send to sync server", enter the server URL (e.g. `http://100.x.y.z:18740`) and a device name, and save.
3. Press **Get started** to move to the home screen.

If the "Today" card on the home screen shows numbers and the timeline renders, you are connected.

### Start recording

In the sidebar, open **Screen recording** → choose the screen to record under "Recording target", and press "Start recording". To exclude apps you do not want recorded, list them under **Settings → Screen recording** → "Apps not to record".

## 3. Connect the AI shared memory (optional)

Only needed if you want Claude Code or Codex work on the timeline.

1. Start [hibi-memory](https://github.com/Kling0012/hibi-memory) on a server in the same network ([AI shared memory](memory.html)).
2. Register it with your AI agents as an MCP server.

   ```sh
   claude mcp add --transport http hibi-memory http://<memory-host>:8377/mcp/
   ```

3. Set `AM_BASE_URL=http://<memory-host>:8377` in the sync server's `.env` and restart. New records are imported every 5 minutes. You can also import manually with "Import now" on the **Status** tab of the management UI.

## 4. View from a phone or iPad

Open `http://<server>:18740/ui/` in the browser of a device on the same Tailscale network. Added to the home screen, it works like an app. The blog is at `http://<server>:18740/blog/`.
