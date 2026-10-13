---
title: Daily reports and blog
---

[日本語](../blog.html) · **English** · [中文](../zh/blog.html)

# Daily reports and blog
{: .no_toc }

1. TOC
{:toc}

## Daily report vs. blog

| | Daily report | Blog |
|---|---|---|
| Purpose | Preserve the day's facts without omission | Preserve the day as writing meant to be read |
| Writer | The sync server's LLM | The sync server's LLM, external AI, or you |
| Publication | Immediately readable | Draft → review → publish |
| Where | The app's **Daily report**, `/ui/#report` | The app's **Blog**, `/blog/` |

## Daily report

Written daily at 23:55 and rewritten at 6:00 the next morning. The material consists of declared work, screen summaries and activity, and AI work.

- Written in six sections (overview of the day, time per work, what you did, what the AI did, how your work and the AI's work relate, and what carries over to tomorrow).
- Every sentence carries an evidence timestamp (e.g. "(14:05)").
- On days with a lot of material, it is pre-summarized in 4-hour chunks with a lighter model before writing.
- "Create now" on the app's daily report screen rewrites it at any time.

## Blog

### Types

| Type | Content |
|---|---|
| Daily summary (`summary`) | One per day. A first-person narrative covering what you tackled, stumbling blocks, decisions, learnings, and next steps. 1,500+ characters (no upper limit) |
| Technical learning (`learning`) | Up to 3 per day. For whoever hits the same problem (your future self): symptom, cause, and fix in order. Ends with evidence timestamps and IDs |
| Post (`post`) | Free-form articles. Written by external AI or by you |

For every type, the rule is: no facts that are not in the material. Before saving, strings that look like keys or tokens (the value of `Bearer`, the value of `password=`, key-like strings of 32+ characters) are masked.

### Automatic writing

The sync server's `BLOG_AUTO` decides what happens to LLM-written articles after the daily report.

| Value | Behavior |
|---|---|
| `off` | Do not write automatically |
| `draft` (default) | Put into drafts. You review and publish |
| `publish` | Publish immediately |

### Post states

| State | Meaning |
|---|---|
| `draft` | Draft. Not listed in the blog |
| `published` | Published. Listed in the blog |
| `archived` | Put away. Not deleted |

The author (`author`) is one of `auto` (the sync server's LLM), `me` (you), or `agent:<name>` (an external AI).

## Having AI write blog posts

You can have an AI such as Claude Code read the day's records and write an article. The sync server also runs as an MCP server.

### Registration

```sh
claude mcp add --transport http hibi http://<server>:18740/mcp
```

### Example prompt

> Read today's material from Hibi and write a blog draft. The topic is rewriting the sync server's search.

The AI proceeds as follows:

1. Gather material with `hibi_get_blog_materials` and `hibi_get_day_timeline`
2. Create a draft with `hibi_create_draft`
3. You read it at `/blog/` or in the app and tell it what to fix → `hibi_update_post`
4. When it looks good, publish with `hibi_publish_post`

{: .important }
Articles written by AI always go to drafts; read them yourself before publishing. This rule is also stated in the MCP tool descriptions.

### MCP tools

| Tool | Kind | What it does |
|---|---|---|
| `hibi_get_day_timeline(date)` | Read | The timeline of the day |
| `hibi_search_records(query, from_time?, to_time?, kinds?)` | Read | Search records |
| `hibi_get_blog_materials(date)` | Read | The day's material (work and time, your records, AI work, daily report) |
| `hibi_list_posts(status?, type?, limit?)` | Read | List posts |
| `hibi_get_post(slug)` | Read | Read a post in full |
| `hibi_create_draft(title, markdown, date, type?, tags?, author_name)` | Write | Create a draft |
| `hibi_update_post(slug, ...)` | Write | Fix a post |
| `hibi_publish_post(slug)` | Write | Publish |
| `hibi_unpublish_post(slug)` | Write | Back to draft |

### Writing via REST

Without MCP, the same is possible over REST ([Sync server API](api.html#blog)).

```sh
B=http://<server>:18740
# Material
curl -s "$B/v1/blog/materials?date=2026-10-13"
# Create a draft
curl -s -X POST $B/v1/blog/posts -H 'Content-Type: application/json' -d '{
  "title": "Rewriting the search", "markdown": "## Background\n\n...",
  "date": "2026-10-13", "type": "post", "tags": ["hibi"],
  "author": "agent:claude", "status": "draft"}'
# Publish
curl -s -X POST $B/v1/blog/posts/<slug>/publish
```

You can also have the sync server's LLM propose a rewrite (`POST /v1/blog/posts/<slug>/rewrite`; it does not save, so apply it with `PATCH` if you like it).
