---
title: 安装设置
---

[日本語](../getting-started.html) · [English](../en/getting-started.html) · **中文**

# 安装设置
{: .no_toc }

本页介绍搭建同步服务器、连接桌面应用，以及按需连接 AI 共享记忆的完整流程。
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## 准备事项

| 事项 | 用途 | 备注 |
|---|---|---|
| 一台常驻的 Linux 机器 | 同步服务器（和共享记忆） | 2 核 / 2 GB 即可。需要 Docker Compose |
| Mac（Apple 芯片）或 Windows | 桌面应用 | 可以多台。不支持 Intel Mac 和 Linux |
| 封闭网络 | 连接设备与服务器 | 推荐 [Tailscale](https://tailscale.com/)。服务器没有认证 |
| 读屏 AI 的 API 密钥 | 屏幕摘要 | [Google AI Studio](https://aistudio.google.com/) 的 Gemini（有免费额度），或 OpenAI 兼容 API |
| 写文章 AI 的 API 密钥 | 工作结束判定、日报、博客 | OpenAI 兼容 API。例如 Zhipu 的 GLM-5.3 / GLM-5.3-Flash |
| 嵌入 API | 搜索 | Cloudflare Workers AI（有免费额度）或 OpenAI 兼容 API。只是试用则不需要 |

{: .warning }
同步服务器和共享记忆没有用户认证。请只暴露在 Tailscale IP 或 `127.0.0.1` 上，不要监听 `0.0.0.0` 或公网 IP。

## 1. 搭建同步服务器

### 先试运行（不使用外部 API）

可以用不依赖外部 API 的开发配置启动。嵌入和 LLM 变成确定性的假实现。

```sh
git clone https://github.com/Kling0012/hibi-server.git
cd hibi-server
docker compose up -d --build
curl -s http://127.0.0.1:18741/healthz
# {"ok":true,"db":"ok","embed_pending":0}
```

在浏览器打开 `http://127.0.0.1:18741/ui/` 即可看到管理界面。

### 以生产模式运行

把 `.env.example` 复制为 `.env`，填写监听地址和 AI 的接入点。

```sh
cp .env.example .env
mkdir -p secrets && chmod 700 secrets
```

`.env` 的主要项目:

| 变量 | 示例 | 含义 |
|---|---|---|
| `PROD_TAILNET_IP` | `100.x.y.z` | 监听的 Tailscale IP（用 `tailscale ip -4` 确认） |
| `PROD_PORT` | `18740` | 监听端口 |
| `EMBED_PROVIDER` | `cloudflare` | 嵌入。`cloudflare` / `openai_compat` / `fake` |
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/coding/paas/v4` | 用于工作结束判定、日报、博客的 OpenAI 兼容 API |
| `AM_BASE_URL` | `http://<memory-host>:8377` | AI 共享记忆。不用则留空 |

密钥放在文件里（不要把值直接写进 `.env` 或 compose 文件）。

```sh
# 写文章 AI 的 API 密钥（1 行）
printf '%s\n' 'YOUR_LLM_KEY' > secrets/llm-key
# 使用 Cloudflare Workers AI 时
cat > secrets/cloudflare.env <<'ENV'
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
ENV
chmod 600 secrets/*
```

启动:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p hibi up -d --build
curl -s http://$PROD_TAILNET_IP:18740/healthz
```

数据库的创建和迁移在启动时自动完成。详细配置请看[同步服务器](server.html)。

{: .important }
嵌入维度（`EMBED_DIM`，默认 1024）在首次启动时固定到数据库。之后修改需要重建数据库，请一开始就确定好。

## 2. 连接桌面应用

### 安装

从 [Releases](https://github.com/Kling0012/hibi/releases) 下载与操作系统对应的版本。

| 系统 | 文件 | 首次注意事项 |
|---|---|---|
| macOS（Apple 芯片） | `Hibi-<版本>.dmg` | 未签名，安装后执行一次 `xattr -dr com.apple.quarantine /Applications/Hibi.app` |
| Windows | `Hibi-<版本>-setup.exe` | 出现 SmartScreen 时点“更多信息”→“仍要运行” |

macOS 在首次开始记录时会请求“屏幕录制”权限。

### 初始设置

首次启动会打开“欢迎使用 Hibi”的设置界面。

1. 在 **AI** 标签页填写读屏 AI 和写文章 AI 的接入点。
   - Gemini: 类型 `gemini_live`，模型 `gemini-3.8-live`。密钥来自文件（如 `~/.config/minecontext/gemini-key`）或直接输入。
   - GLM 等 OpenAI 兼容: 类型 `openai_compat`，填写 base URL、模型名和密钥。
   - 可用“测试连接”确认是否可达。
2. 在 **同步** 标签页打开“发送到同步服务器”，填入服务器 URL（如 `http://100.x.y.z:18740`）和设备名并保存。
3. 按**开始使用**进入主界面。

主界面的“今日”出现数字、时间线显示出来，就说明连接成功了。

### 开始记录

在侧边栏打开**屏幕记录** → 在“记录对象”里选择屏幕，按“开始记录”。不想记录的应用写在**设置 → 屏幕记录**的“不记录的应用”里。

## 3. 连接 AI 共享记忆（可选）

只有想把 Claude Code 或 Codex 的工作放到时间线上时才需要。

1. 在同一网络内的服务器上启动 [hibi-memory](https://github.com/Kling0012/hibi-memory)（[AI 共享记忆](memory.html)）。
2. 把它作为 MCP 服务器注册到 AI 智能体。

   ```sh
   claude mcp add --transport http hibi-memory http://<memory-host>:8377/mcp/
   ```

3. 在同步服务器的 `.env` 里写入 `AM_BASE_URL=http://<memory-host>:8377` 并重启。每 5 分钟导入一次新记录。也可以在管理界面的**状态**标签页用“立即导入”手动导入。

## 4. 从手机、iPad 查看

在同一 Tailscale 网络的设备浏览器里打开 `http://<server>:18740/ui/`。添加到主屏幕后可以像应用一样使用。博客在 `http://<server>:18740/blog/`。
