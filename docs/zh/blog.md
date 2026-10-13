---
title: 日报与博客
---

[日本語](../blog.html) · [English](../en/blog.html) · **中文**

# 日报与博客
{: .no_toc }

1. TOC
{:toc}

## 日报与博客的区别

| | 日报 | 博客 |
|---|---|---|
| 目的 | 无遗漏地保留当天的事实 | 作为给人阅读的文章保留 |
| 作者 | 同步服务器的 LLM | 同步服务器的 LLM、外部 AI、自己 |
| 公开 | 立即可读 | 草稿 → 确认 → 发布 |
| 位置 | 应用的**日报**、`/ui/#report` | 应用的**博客**、`/blog/` |

## 日报

每天 23:55 撰写当天内容，次日早晨 6:00 重写。材料为已声明的工作、屏幕摘要与活动、AI 的工作。

- 分 6 个章节撰写（今日概要、每项工作的时长、自己做的事、AI 做的事、自己与 AI 工作的关系、留到明天的事）。
- 每句话附带依据时刻（如“（14:05）”）。
- 材料过多的日子，先用轻量模型按 4 小时分块预总结再写。
- 应用日报界面的“立即创建”可随时重写。

## 博客

### 种类

| 种类 | 内容 |
|---|---|
| 一日总结（summary） | 每天 1 篇。以第一人称的敬体，把做过的事、遇到的坎、判断、收获、下一步作为一段故事来写。1500 字以上（不设上限） |
| 技术心得（learning） | 每天最多 3 篇。面向遇到同样问题的人（未来的自己），依次写症状、原因、对策。结尾附依据时刻和 ID |
| 文章（post） | 自由文章。由外部 AI 或自己撰写 |

任何种类都遵循一条规则: 不写材料中没有的事实。保存前会打码疑似密钥或令牌的字符串（`Bearer` 的值、`password=` 的值、32 字符以上的疑似密钥串）。

### 自动撰写设置

用同步服务器的 `BLOG_AUTO` 决定日报之后 LLM 写出的文章如何处理。

| 值 | 行为 |
|---|---|
| `off` | 不自动撰写 |
| `draft`（默认） | 放入草稿。自己确认后发布 |
| `publish` | 立即发布 |

### 文章状态

| 状态 | 含义 |
|---|---|
| `draft` | 草稿。不出现在博客列表 |
| `published` | 已发布。出现在博客列表 |
| `archived` | 已归档。不会删除 |

作者（`author`）为 `auto`（同步服务器的 LLM）、`me`（自己）、`agent:<名称>`（外部 AI）之一。

## 让 AI 写博客

可以让 Claude Code 等 AI 读取当天记录并撰写文章。同步服务器同时也是一个 MCP 服务器。

### 注册

```sh
claude mcp add --transport http hibi http://<server>:18740/mcp
```

### 请求示例

> 读取 Hibi 今天的材料，写一篇博客草稿。题材是重构同步服务器搜索的经过。

AI 按以下顺序推进。

1. 用 `hibi_get_blog_materials` 和 `hibi_get_day_timeline` 收集材料
2. 用 `hibi_create_draft` 创建草稿
3. 自己在 `/blog/` 或应用里阅读，把要修改的地方告诉它 → `hibi_update_post`
4. 满意后用 `hibi_publish_post` 发布

{: .important }
AI 写的文章务必先入草稿，自己读过之后再发布。MCP 工具的说明中也写有这条规则。

### MCP 工具

| 工具 | 种类 | 内容 |
|---|---|---|
| `hibi_get_day_timeline(date)` | 读 | 当天的时间线 |
| `hibi_search_records(query, from_time?, to_time?, kinds?)` | 读 | 搜索记录 |
| `hibi_get_blog_materials(date)` | 读 | 当天的材料（工作与时长、自己的记录、AI 的工作、日报） |
| `hibi_list_posts(status?, type?, limit?)` | 读 | 文章列表 |
| `hibi_get_post(slug)` | 读 | 文章全文 |
| `hibi_create_draft(title, markdown, date, type?, tags?, author_name)` | 写 | 创建草稿 |
| `hibi_update_post(slug, ...)` | 写 | 修改文章 |
| `hibi_publish_post(slug)` | 写 | 发布 |
| `hibi_unpublish_post(slug)` | 写 | 退回草稿 |

### 用 REST 撰写

不用 MCP 也可以通过 REST 完成同样的事（[同步服务器 API](api.html#博客)）。

```sh
B=http://<server>:18740
# 材料
curl -s "$B/v1/blog/materials?date=2026-10-13"
# 创建草稿
curl -s -X POST $B/v1/blog/posts -H 'Content-Type: application/json' -d '{
  "title": "重构了搜索", "markdown": "## 缘起\n\n...",
  "date": "2026-10-13", "type": "post", "tags": ["hibi"],
  "author": "agent:claude", "status": "draft"}'
# 发布
curl -s -X POST $B/v1/blog/posts/<slug>/publish
```

还可以让同步服务器的 LLM 提出改写方案（`POST /v1/blog/posts/<slug>/rewrite`；不会保存，满意的话用 `PATCH` 保存）。
