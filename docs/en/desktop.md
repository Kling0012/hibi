---
title: Desktop app
parent: English
nav_order: 4
---

[日本語](../desktop.html) · **English** · [中文](../zh/desktop.html)

# Desktop app (hibi)
{: .no_toc }

An app for Apple silicon Macs and Windows. It records the screen, has an AI summarize it, and sends the results to the sync server. Repository: [Kling0012/hibi](https://github.com/Kling0012/hibi)
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## How it works

The app consists of two parts.

| Part | Contents | Role |
|---|---|---|
| `frontend/` | Electron + React | Screen capture, declaring work, display, settings |
| `opencontext/` | Python (FastAPI), bundled with the app | AI summarization, to-dos / hints, sending to the sync server |

The Python side listens only on `127.0.0.1:1733` and is not reachable from outside.

```
Screen (up to 1 frame/sec) + frontmost app name, window title, URL
   │
   ▼
ScreenLiveMonitor (Python)
   ├─ discard if the app is excluded from recording
   ├─ stream continuously to Gemini Live; every 30 seconds summarize
   │  "what is happening" → activity
   └─ when the window changes or the screen changes a lot, record the
      on-screen text accurately with GLM → screen_summary
   │
   ▼
Local storage + send queue (outbox) ──▶ sync server (sent in batches;
                                         recording continues on failure)
```

The screen images themselves are never sent to the server. Only summaries and metadata are sent.

## Screens

| Sidebar | Contents |
|---|---|
| Home | Declaring work, today's numbers, today's lanes, to-dos, hints, chatting with the AI |
| Timeline | The sync server's timeline (lanes, list, heatmap) |
| Daily report | The sync server's daily reports |
| Blog | The sync server's blog |
| Search | Search over your records and AI work |
| Screen recording | Start / stop recording, recording target, list of on-device records |
| Settings | AI / Screen recording / Sync / App |

Timeline, daily report, blog, and search embed the sync server's views directly in the app — the same ones you see on a phone. When the sync server is unreachable, a notice and a button to the settings appear.

## AI connections

Under **Settings → AI**, you configure the AI providers (connections) to use and their priority order per purpose.

### Connections

| Field | Description |
|---|---|
| ID | Any name. Used when ordering purposes |
| Type | `gemini_live` (Google's Live API) or `openai_compat` (OpenAI-compatible API) |
| Model | e.g. `gemini-3.8-live`, `glm-5.3-flash` |
| Base URL | For `openai_compat`. e.g. `https://open.bigmodel.cn/api/coding/paas/v4` |
| API key | Path to a file (`~` allowed) or direct input. Directly entered values are not shown again |

### Purposes

| Purpose | Where it is used | Default order |
|---|---|---|
| Vision | Screen summarization | Gemini Live → GLM-5.3-Flash |
| Chat | "Chat with AI" | GLM-5.3-Flash → GLM-5.3 |
| Structured | JSON output for to-dos and hints | GLM-5.3-Flash |
| Exact | Transcription of on-screen text | GLM-5.3-Flash |

Connections are used from the top of the order; on failure it switches to the next. The failures that trigger a switch are rate limits (429), quota exhaustion, server errors (5xx), timeouts, disconnects, and model mismatches. A failed connection is rested for a while (the `Retry-After` seconds when present, otherwise 60 seconds doubling up to 15 minutes).

Leaving a purpose empty stops only the features that use it; everything else keeps working.

The app does not run searches itself — it uses the sync server's hybrid search (the search inside chat, too). That is why the embedding purpose is empty by default.

### About Gemini Live

Gemini Live (`gemini-3.8-live`) has a generous free tier and suits continuous streaming of the screen. At one frame per second it consumes roughly 8,000 tokens per minute.

- One connection is kept per purpose, and reconnected automatically when it drops (Live connections sometimes go silent after about 40–45 seconds).
- Responses come back as audio, and their transcript is used as text.
- GLM-5.3-Flash is more accurate with text, so the exact recording of on-screen text uses GLM.

## Screen recording

**Settings → Screen recording**

| Setting | Default | Description |
|---|---|---|
| Pause recording | Off | While on, no frames are captured at all |
| Live frame rate | 1 | Frames per second (0.2–1) |
| Live summary interval | 30 s | Interval at which the streamed screen is summarized as activity |
| Minimum text interval | 20 s | Lower bound between transcriptions of on-screen text |
| Change threshold | 10 | How much the screen must change to trigger a transcription (dhash distance; higher is less sensitive) |
| Apps not to record | 1Password, Bitwarden, KeePassXC | Below |
| Mark me away after | 5 min | Pauses recording after this long without input (3 / 5 / 10 / 15 minutes, or disabled) |

### Away

Every 15 seconds, the app checks the OS's time of last input. When the timeout is exceeded, it considers you away and pauses screen recording; recording resumes when you return. Locking the screen or putting the computer to sleep marks you away immediately. If you paused recording manually, returning does not resume it.

Away periods of at least 1 minute are sent to the sync server as `kind=away` records (`meta.reason` is `idle` / `lock` / `suspend`). While you are away, Home and "Screen recording" show "Away (since 14:05)".

### Apps not to record

When the frontmost app name contains one of the strings listed here, that screen is discarded (it is not sent to the AI either). List your password manager, banking apps, private chats, and so on. One entry per line.

## Sync

**Settings → Sync**

| Setting | Description |
|---|---|
| Send to sync server | Even when off, records accumulate locally and are sent once turned on |
| Server URL | e.g. `http://100.x.y.z:18740` |
| Device name | Lane name in the timeline. Defaults to the host name |

Below, the device ID, the last send time, the unsent count, and the most recent error are shown. Sends are capped at 100 records each, and retries back off from 5 to 60 seconds on failure. Sending the same record twice does not duplicate it.

## To-dos and hints

The "To-dos" and "Hints" cards on the home screen are generated by the AI from screen recordings (transcripts and activity summaries) and that day's work on the sync server.

- **To-dos** (every 30 minutes, up to 3 at a time): picks up only "things that need doing" visible on the screen — replies demanded by incoming mail or notifications, notes you wrote yourself, work stalled on an error, work ended by idling, and so on. Pages you merely looked at are not picked up. Each item carries the time and screen it is based on.
- **Hints** (hourly, up to 2): insights such as seeing the same error repeatedly, working without a break, or the screen drifting away from the declared work. Nothing is shown without evidence. Content already shown within the last 24 hours is not repeated.

Strings that look like passwords, tokens, or e-mail addresses are never written in to-dos or hints.

To-dos the AI discovers are registered on the sync server (`POST /v1/todos`, `source=auto`). If the server is unreachable or sync is turned off, they stay locally and are sent later. The home screen's to-do list embeds the sync server's view, so they appear alongside to-dos you added yourself.

## Display language

**Settings → App → "Display language"** (Auto / Japanese / English / Chinese). Changes take effect immediately, including the tray menu. The same language is passed to the embedded sync server views.

## Auto-update

**Settings → App → "Check for updates automatically"** (on by default). It checks GitHub releases 30 seconds after launch and then every 6 hours.

| OS | Behavior |
|---|---|
| Windows | Downloads in the background and shows "Restart to update" at the bottom of the sidebar |
| Mac | Unsigned, so it does not replace itself automatically. Pressing "A new version is available" opens the download page |

While on a development build (`-dev.N`), you also receive development-build updates.

## Settings files

Values changed in the UI are saved to `config/user_setting.yaml` in the app's data folder. Defaults live in the bundled `config/config.yaml`.

| OS | Data folder |
|---|---|
| macOS | `~/Library/Application Support/Hibi` |
| Windows | `%APPDATA%\Hibi` |

The default locations for API key files are `~/.config/minecontext/gemini-key` and `~/.config/minecontext/zhipu-key` (changeable via the `GEMINI_KEY_FILE` / `ZHIPU_KEY_FILE` environment variables).

## Development

```sh
git clone https://github.com/Kling0012/hibi.git
cd hibi
uv sync                                  # Python 3.12
uv run python -m opencontext.cli start   # backend
cd frontend && pnpm install && pnpm run dev
```

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js 22 or newer with pnpm 10.

### Release builds

Because the Python part is bundled with PyInstaller, **build each OS on that OS**. Running the GitHub Actions `release.yml` manually builds the macOS (Apple silicon) and Windows versions and attaches them to a draft release.

```sh
gh workflow run release.yml -R Kling0012/hibi -f tag=v0.1.0
```

To build locally, run `pnpm run build:mac` / `build:win` in `frontend/`. Only Apple silicon Macs and Windows are supported.

## Differences from the original project

Changed from [volcengine/MineContext](https://github.com/volcengine/MineContext):

- Renamed to Hibi with a new icon (existing MineContext data is migrated on first launch)
- AI is now chosen per purpose from multiple connections, with automatic failover (Gemini Live, OpenAI-compatible)
- Screen recording is now two-stage: continuous streaming to the Live API plus accurate transcription on change
- Added sending to the sync server, declaring work, and views of the sync server
- Removed folder / document / web-page ingestion, Doubao / Ark, completion features, and the old web UI
- Localized the UI to Japanese and rebuilt the sidebar and home screen
- Updated to Electron 44, React 19, Python 3.12 / uv
