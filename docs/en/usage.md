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
- **To-dos** / **Hints** — a to-do list you add to and the AI discovers items for, plus insights from your records

The bottom of the sidebar always shows the work currently declared and its elapsed time.

## Declaring work

Type what you are about to do in the input box on the home screen and press Enter (or "Start"); the work begins at that moment. There are no shortcut keys or tray actions — you declare from the app's screen.

- **Ending** — you do not have to do anything. The sync server checks the screen recordings every few minutes and has the AI decide whether the work has finished. When it decides the work is done, the end time is set to **the time of the last related record** (not the time of the decision).
- **Idle** — if no record arrives from any device for 15 minutes, the work ends there.
- **Ending manually** — pressing "End" finishes it at that moment.
- **Moving to the next work** — declaring another work while one is running ends the previous one at that moment.
- **Recent work** — pressing a suggestion under the input box starts work with the same name.

Work ended by the AI or by idling becomes "unconfirmed". Tap the work in the timeline of the mobile management UI to fix the times and name and confirm it.

## To-dos

Under **To-dos** on the home screen (to the right of "Today" on a phone), write what you need to do and press "Add". To-dos the AI discovers from screen recordings appear in the same list, labeled "Found by AI". Similar items already on the list are not added again.

- **Time spent is added automatically.** Every 5 minutes, the sync server checks screen recordings, asks the AI which to-do you were working on, and adds that time. The to-do you are currently working on shows "Working on it".
- Added time appears as bars in the timeline's "To-dos" lane. If it is wrong, tap the bar and choose "Remove this time".
- Press the circle to mark a to-do done, or × to delete it. "Show completed" also shows finished items.
- Time spent away (below) is not added.

## Away

After a while without input (5 minutes by default), the app considers you away and pauses screen recording. Locking the screen or putting the computer to sleep also marks you away immediately. Recording resumes when you return, and time away appears on the timeline as gray diagonal stripes.

- Time away is deducted from declared work time and to-do time.
- If you are away for 30 minutes or more during work, that work ends at the time you left. It stays unconfirmed so you can fix it later.
- Even when you are away from your Mac, working on another PC during that time does not count as away.
- Set the time before you are considered away under **Settings → Screen recording → "Mark me away after"** (3 / 5 / 10 / 15 minutes, or disabled).

## Timeline

The day is shown as lanes flowing horizontally.

| Lane | Content |
|---|---|
| Work | Declared work. Bar length is the duration |
| Per device (e.g. MacBook) | That device's screen summaries and activity |
| To-dos | Time the AI judged you were working on a to-do |
| Per AI (e.g. AI claude) | Work that AI agents left in the shared memory |

- Zoom with `−` / `＋`, and jump to now with "Now". The red line is the current time.
- Tap a dot or bar to see its contents.
- The list below is all records grouped by time. Filter by "Work", "My records", or "AI work".
- The heatmap on the right covers the last 5 weeks. Switch between "Me", "AI", and "Work time"; darker means more. Tap a day to jump to it.

## Share your work

Use "Share your work" under **Work time** in "Today" to turn your records into a single image (1200×675) and post it to X.

- **Period**: Today / Last 7 days / Last 30 days
- **Colors**: 5 themes
- **What to include**: work time, PC activity time, AI activity time, completed to-dos, recording streak, work breakdown, and a chart. "Hide work names" replaces work names with labels such as "Work A"
- **Heading, comment, and name**: text to include in the image. The name appears at the bottom left
- **Post text**: generated automatically; you can edit it

Press "Post to X" on a phone to send the image to X through the share menu. On a PC, the image is copied to the clipboard before the X post screen opens, so paste it there (if copying fails, the image is saved). The image is generated entirely in the browser and is not uploaded anywhere.

## Daily report

Every day at 23:55, the AI writes a daily report from that day's records. At 6:00 the next morning, it rewrites it including the late-night records. Switch days and read them under **Daily report** in the sidebar. "Create now" regenerates it at any time.

Daily reports include timestamps as evidence (e.g. "(14:05)"), so you can check them against the timeline.

## Blog

If the daily report is "a complete summary of the day's facts", the blog is "writing meant to be read". See [Daily reports and blog](blog.html) for details.

## Search

Under **Search** in the sidebar, you can search your records and AI work together. It finds records with similar meaning even when the wording does not match.

## Phone and iPad

The same views are available at `http://<server>:18740/ui/`. The bottom tabs are "Today", "Daily report", "Search", and "Status". "Status" shows the state of the server and imports.

## Display language

Japanese, English, and Simplified Chinese are supported. By default, the app follows the OS or browser language.

- App: **Settings → App → "Display language"**
- Mobile view: "Display language" on the "Status" tab
- Blog: the switch at the top right

The text of AI-written daily reports, blog posts, to-dos, and hints is not translated.

## When you want to stop recording

- Pause for a while: **Settings → Screen recording → "Pause recording"**
- Exclude specific apps: **Settings → Screen recording → "Apps not to record"** (one substring of an app name per line)
- Stop sending to the server: **Settings → Sync → turn off "Send to sync server"** (records accumulate locally and are sent once you turn it back on)
