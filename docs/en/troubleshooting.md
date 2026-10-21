---
title: Troubleshooting
parent: English
nav_order: 9
---

[日本語](../troubleshooting.html) · **English** · [中文](../zh/troubleshooting.html)

# Troubleshooting
{: .no_toc }

1. TOC
{:toc}

## The app

### The setup screen appears on every launch

This happens when neither the vision nor the structured purpose has a connection. Add connections under **Settings → AI** → "Priority per purpose" and save.

### macOS says the app "is damaged and can't be opened"

It is unsigned. Run this once:

```sh
xattr -dr com.apple.quarantine /Applications/Hibi.app
```

### Screen recording stays "stopped"

- Check that you selected a "Recording target" under **Screen recording** and pressed "Start recording".
- macOS: check that Hibi is enabled under **System Settings → Privacy & Security → Screen Recording**, then restart the app.
- Check that "Pause recording" under **Settings → Screen recording** is off.
- While the frontmost app is listed under "Apps not to record", nothing is recorded.

### Screen summaries do not appear or are slow

- Use "Test connection" under **Settings → AI** to check that a vision connection is reachable.
- When the Gemini free tier is exhausted, it switches to the next connection (GLM etc.) automatically. Summaries stop when there is no next connection.
- Summaries come every 30 seconds (the live summary interval). They do not appear instantly.

### Timeline, daily report, or blog says "cannot connect to the sync server"

- Check that the URL under **Settings → Sync** looks like `http://<server>:18740` (no trailing `/ui`).
- Check that the PC is connected to Tailscale.
- Open `http://<server>:18740/healthz` in a browser and confirm it returns `{"ok":true,...}`.

### Timezone setting for the daily timeline tool

The chat tool that retrieves one day's timeline first uses an IANA timezone name from the `TZ` environment variable. If unset, it checks the `/etc/localtime` symlink. Set `TZ` on Windows or other systems without this symlink. Examples: `Asia/Tokyo` for Japan, `UTC` for UTC, or `America/New_York` for New York. IANA names also preserve daylight saving transitions.

On Windows, add `TZ` to your user environment variables with a value such as `Asia/Tokyo`. Fully exit the app, sign out of Windows and sign back in, then launch the app. On macOS, set `TZ` in the environment used to launch the app and restart it.

If the name is invalid or no timezone can be detected, the tool returns an error asking you to configure it and stops only the daily lookup. It does not fall back to UTC. Search and screen recording remain available. This setting is separate from the sync server's `TZ_DEFAULT`.

### The "unsent" count does not go down

Look at "Most recent error" under **Settings → Sync**. While the server is unreachable, records accumulate locally and are sent automatically once it is reachable again. No records are lost.

## Declaring work

### The declaration input box cannot be clicked

You cannot declare work while disconnected from the sync server. Check the yellow notice under the input box.

### Work ended on its own

Either the AI decided it was finished, or no records arrived from any device for 15 minutes. Tap the work in the timeline of the management UI to fix the end time or confirm it. If the decision comes too early, raise the server's `SESSION_END_THRESHOLD` (default 0.75).

### Work never ends

When no LLM is configured (`LLM_BASE_URL` empty), AI detection does not run. Only the 15-minute idle rule and manual ending apply.

## Sync server

### AI work is not imported

- Check the import's "last success" and "error" under **Status** in the management UI, and press "Import now".
- Check that `AM_BASE_URL` is set.
- Check that the container can reach the shared memory.

  ```sh
  docker compose -p hibi exec api python -c "import urllib.request;print(urllib.request.urlopen('http://<memory-host>:8377/health').read())"
  ```

  If unreachable, the host firewall (e.g. ufw's default DROP) may be blocking traffic from the container.

### Semantically close results do not show up in search

- If `/healthz`'s `embed_pending` stays large, embedding is stalled. Check the API logs (`docker compose -p hibi logs api`).
- With `EMBED_PROVIDER=fake`, semantic search is mostly ineffective.

### Daily reports or blog posts are not generated

When the LLM is unreachable, they are simply not created and only a log entry remains. Pressing "Create now" on the daily report tab of the management UI shows the error.

### Changing the embedding dimension

The dimension is fixed in the DB on first startup. To change it, take a backup and rebuild the DB ([Sync server](server.html#backups)).
