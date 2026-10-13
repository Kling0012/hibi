---
title: Usage
parent: English
nav_order: 3
---

[日本語](../usage.html) · **English** · [中文](../zh/usage.html)

# Usage
{: .no_toc }

1. TOC
{:toc}

## Home

Opening the desktop app shows a home screen that tells you today at a glance.

- **What are you going to do?** — declaring work (below)
- **Today** — total time of declared work, PC activity time and counts, AI work time and counts, whether screen recording is running
- **Timeline** — today's lanes (below)
- **Today's to-dos** / **Hints** — a to-do list and insights the AI generates from your records

The bottom of the sidebar always shows the work currently declared and its elapsed time.

## Declaring work

Type what you are about to do in the input box on the home screen and press Enter (or "Start"); the work begins at that moment. There are no shortcut keys or tray actions — you declare from the app's screen.

- **Ending** — you do not have to do anything. The sync server checks the screen recordings every few minutes and has the AI decide whether the work has finished. When it decides the work is done, the end time is set to **the time of the last related record** (not the time of the decision).
- **Idle** — if no record arrives from any device for 15 minutes, the work ends there.
- **Ending manually** — pressing "End" finishes it at that moment.
- **Moving to the next work** — declaring another work while one is running ends the previous one at that moment.
- **Recent work** — pressing a suggestion under the input box starts work with the same name.

Work ended by the AI or by idling becomes "unconfirmed". Tap the work in the timeline of the mobile management UI to fix the times and name and confirm it.

## Timeline

The day is shown as lanes flowing horizontally.

| Lane | Content |
|---|---|
| Work | Declared work. Bar length is the duration |
| Per device (e.g. MacBook) | That device's screen summaries and activity |
| Per AI (e.g. AI claude) | Work that AI agents left in the shared memory |

- Zoom with `−` / `＋`, and jump to now with "Now". The red line is the current time.
- Tap a dot or bar to see its contents.
- The list below is all records grouped by time. Filter by "Work", "My records", or "AI work".
- The heatmap on the right covers the last 5 weeks. Switch between "Me", "AI", and "Work time"; darker means more. Tap a day to jump to it.

## Daily report

Every day at 23:55, the AI writes a daily report from that day's records. At 6:00 the next morning, it rewrites it including the late-night records. Switch days and read them under **Daily report** in the sidebar. "Create now" regenerates it at any time.

Daily reports include timestamps as evidence (e.g. "(14:05)"), so you can check them against the timeline.

## Blog

If the daily report is "a complete summary of the day's facts", the blog is "writing meant to be read". See [Daily reports and blog](blog.html) for details.

## Search

Under **Search** in the sidebar, you can search your records and AI work together. It finds records with similar meaning even when the wording does not match.

## Phone and iPad

The same views are available at `http://<server>:18740/ui/`. The bottom tabs are "Today", "Daily report", "Search", and "Status". "Status" shows the state of the server and imports.

## When you want to stop recording

- Pause for a while: **Settings → Screen recording → "Pause recording"**
- Exclude specific apps: **Settings → Screen recording → "Apps not to record"** (one substring of an app name per line)
- Stop sending to the server: **Settings → Sync → turn off "Send to sync server"** (records accumulate locally and are sent once you turn it back on)
