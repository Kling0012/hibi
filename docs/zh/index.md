---
title: 简介
---

[日本語](../index.html) · [English](../en/index.html) · **中文**

# Hibi

Hibi（意为“日日”）是一个个人工作记录系统，把**你在电脑上做的事**和**AI 智能体做的事**汇成同一条时间线。

- AI 读取电脑屏幕，自动记录“现在正在做什么”。
- 声明“接下来要做○○”之后，结束时间由 AI 根据屏幕情形判断，工作时间因此被保留下来。
- Claude Code、Codex 等 AI 留在共享记忆里的工作，也会带时间戳排列在同一条时间线上。
- AI 根据当天的记录撰写日报和博客文章。
- 记录汇集到自己的服务器，无论在 Mac 还是 Windows 上工作，都能从手机或 iPad 看到同一份记录。

## 三个仓库

Hibi 由职责不同的三个仓库组成。每个都能单独运行，组合起来就是完整整体。

| 仓库 | 职责 | 运行位置 |
|---|---|---|
| [hibi](https://github.com/Kling0012/hibi) | 桌面应用。屏幕记录、声明工作、查看 | 自己的 Mac / Windows |
| [hibi-server](https://github.com/Kling0012/hibi-server) | 同步服务器。记录汇集、搜索、日报/博客生成、手机用界面 | 一台常驻的小服务器 |
| [hibi-memory](https://github.com/Kling0012/hibi-memory) | AI 共享记忆。AI 智能体留存工作内容的地方 | 常驻服务器（可与同步服务器同台） |

```
 Mac / Windows                         同步服务器（hibi-server）
┌──────────────────────┐    记录   ┌──────────────────────────────┐
│ Hibi 桌面应用         │ ───────▶ │ PostgreSQL + pgvector         │
│  屏幕记录 / AI 摘要   │           │ 时间线 / 搜索                  │
│  声明工作             │ ◀─────── │ 工作结束判定                   │
│                      │    查看   │ 日报 / 博客                    │
└──────────────────────┘           │ 手机 / iPad 用界面 /ui         │
                                   └──────────────▲───────────────┘
                                                  │ 定期导入（只读）
                                   ┌──────────────┴───────────────┐
 Claude Code / Codex 等 ───────▶   │ AI 共享记忆（hibi-memory）     │
         通过 MCP 记录 / 搜索       └──────────────────────────────┘
```

## 功能一览

| 功能 | 说明 | 详情 |
|---|---|---|
| 屏幕记录 | 以每秒最多 1 帧把屏幕发给 AI，记录活动摘要和屏幕文字 | [桌面应用](desktop.html) |
| AI 自动切换 | 优先使用 Gemini Live（免费额度），达到上限或故障时自动切换到 GLM 等 | [桌面应用](desktop.html#ai-接入点) |
| 声明工作 | 写下“接下来要做○○”即可开始。结束由 AI 判断，之后可以确认和修改 | [使用方法](usage.html#声明工作) |
| 时间线 | 按工作、按设备、按 AI 分泳道横向排列一天，用一个月的热力图回顾 | [使用方法](usage.html#时间线) |
| 搜索 | 结合语义相近和文字一致的混合搜索 | [同步服务器 API](api.html#搜索) |
| 日报 | 每晚 AI 根据当天记录撰写日报（次日早晨重写） | [日报与博客](blog.html) |
| 博客 | 与日报分开，AI 起草可读性强的博客文章。也可以让外部 AI 撰写 | [日报与博客](blog.html) |
| AI 工作记录 | 把 AI 智能体留在共享记忆里的工作导入时间线 | [AI 共享记忆](memory.html) |

## 接下来读什么

1. [安装设置](getting-started.html) — 从搭建服务器到连接应用
2. [使用方法](usage.html) — 日常使用
3. [桌面应用](desktop.html) / [同步服务器](server.html) / [AI 共享记忆](memory.html) — 各部分详解
4. [同步服务器 API](api.html) — REST 与 MCP 一览
5. [疑难解答](troubleshooting.html)

## 前提与注意事项

- **个人使用**。同步服务器和共享记忆没有用户认证。请只在 Tailscale 之类的封闭网络或 localhost 内使用，不要直接暴露到互联网。
- 屏幕内容会发送到所配置的 AI 的 API。密码管理器等不想发送的应用请通过[排除设置](desktop.html#不记录的应用)排除。
- Hibi 基于 [volcengine/MineContext](https://github.com/volcengine/MineContext)（Apache License 2.0）重新构建，功能和结构与原项目差异很大。
