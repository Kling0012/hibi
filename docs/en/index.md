---
title: English
nav_order: 100
has_children: true
---

[日本語](../index.html) · **English** · [中文](../zh/index.html)

# Hibi

Hibi (meaning "day by day") is a personal work-tracking system that brings **what you did on your PC** and **what your AI agents did** together into a single timeline.

- An AI reads your PC screen and automatically records "what you are doing right now".
- When you declare "I'm about to do X", the AI later decides from the screen when the work finished, so the time spent is preserved.
- Work that AI agents such as Claude Code or Codex leave in the shared memory appears on the same timeline with timestamps.
- From each day's records, the AI writes a daily report and a blog post.
- Records are collected on your own server, so whether you work on a Mac or Windows, you can view the same records from your phone or iPad.

## The three components

Hibi consists of three components with different roles, all included in this repository. Each works on its own, and together they form the whole. To start just the sync server, run `docker compose up -d --build` from the repository root (add `--profile memory` to run the shared memory as well).

| Location | Role | Where it runs |
|---|---|---|
| [`frontend/`](https://github.com/Kling0012/hibi/tree/main/frontend) | Desktop app. Screen recording, declaring work, viewing | Your Mac / Windows |
| [`server/`](https://github.com/Kling0012/hibi/tree/main/server) | Sync server. Aggregating records, search, daily report / blog generation, mobile-friendly views | One small always-on server |
| [`memory/`](https://github.com/Kling0012/hibi/tree/main/memory) (optional) | Shared memory for AI. Where AI agents leave their work | An always-on server (can be the same one as the sync server) |

```
 Mac / Windows                         Sync server (server/)
┌──────────────────────┐   records  ┌──────────────────────────────┐
│ Hibi desktop app     │ ───────▶   │ PostgreSQL + pgvector         │
│  screen recording    │            │ timeline / search             │
│  AI summaries        │ ◀───────   │ work-end detection            │
│  declaring work      │   viewing  │ daily report / blog           │
└──────────────────────┘            │ mobile / iPad view at /ui     │
                                    └──────────────▲───────────────┘
                                                   │ periodic import (read-only)
                                    ┌──────────────┴───────────────┐
 Claude Code / Codex etc. ───────▶  │ AI shared memory (memory/)    │
         record / search via MCP    └──────────────────────────────┘
```

## What it can do

| Feature | Description | Details |
|---|---|---|
| Screen recording | Sends the screen to an AI at up to one frame per second and records activity summaries and on-screen text | [Desktop app](desktop.html) |
| AI fallback | Prefers Gemini Live (free tier) and automatically switches to GLM and others on limits or failures | [Desktop app](desktop.html#ai-connections) |
| Declaring work | Start by saying "I'm about to do X". The AI decides when it ends, and you can confirm or fix it later | [Usage](usage.html#declaring-work) |
| Timeline | Lays out the day in lanes per work, per device, and per AI, with a one-month heatmap for looking back | [Usage](usage.html#timeline) |
| To-dos | Automatically adds time spent from your records to to-dos you add yourself | [Usage](usage.html#to-dos) |
| Away detection | Pauses recording after a while without input and deducts time away from work time | [Usage](usage.html#away) |
| Share your work | Choose a period and items to include, turn them into a single image, and post it to X | [Usage](usage.html#share-your-work) |
| Three languages | The UI and documentation support Japanese, English, and Chinese | [Usage](usage.html#display-language) |
| Search | Hybrid search combining semantic similarity and text matching | [Sync server API](api.html#search) |
| Daily report | Every night the AI writes a daily report from that day's records (rewritten the next morning) | [Daily reports and blog](blog.html) |
| Blog | Separately from daily reports, the AI drafts readable blog posts. You can also have external AI write them | [Daily reports and blog](blog.html) |
| AI work records | Imports the work that AI agents left in the shared memory into the timeline | [AI shared memory](memory.html) |

## Read next

1. [Setup](getting-started.html) — from setting up a server to connecting the app
2. [Usage](usage.html) — everyday usage
3. [Desktop app](desktop.html) / [Sync server](server.html) / [AI shared memory](memory.html) — details of each part
4. [Sync server API](api.html) — REST and MCP reference
5. [Troubleshooting](troubleshooting.html)

## Assumptions and cautions

- This is for **personal use**. The sync server and the shared memory have no user authentication. Use them only within a closed network such as Tailscale, or on localhost. Do not expose them directly to the internet.
- The contents of your screen are sent to the AI APIs you configure. Exclude apps you do not want to send, such as password managers, via the [exclusion list](desktop.html#apps-not-to-record).
- Hibi is a rebuild based on [volcengine/MineContext](https://github.com/volcengine/MineContext) (Apache License 2.0). Its features and structure differ substantially from the original project.
