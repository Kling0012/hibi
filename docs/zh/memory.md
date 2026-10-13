---
title: AI 共享记忆
parent: 中文
nav_order: 6
---

[日本語](../memory.html) · [English](../en/memory.html) · **中文**

# AI 共享记忆（hibi-memory）
{: .no_toc }

供 Claude Code、Codex 等 AI 智能体留存决定过的事、调查过的事、修复过的事，并让其他 AI、其他设备也能检索的记忆存放处。Hibi 把留在这里的记录作为“AI 的工作”放上时间线。仓库: [Kling0012/hibi-memory](https://github.com/Kling0012/hibi-memory)
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## 构成

| 服务 | 内容 | 监听 |
|---|---|---|
| gateway | FastAPI + MCP。对 AI 开放的唯一入口 | `8377`（REST、`/mcp/`、`/docs`） |
| valkey | 短期记忆（带期限的工作备忘） | `127.0.0.1:16379` |
| falkordb | 长期记忆（Graphiti 的时序知识图谱） | `127.0.0.1:16380` |

AI 只使用 gateway 的 MCP 或 REST，不直接接触数据库。

### 记忆如何被保存

1. AI 调用 `memory_remember` 时，gateway 立即保存原始记录并返回 `enrichment_status=pending`（几十毫秒）。
2. 后台逐条用 LLM 抽取人物、事物、关系并加入图谱（可能需要几分钟）。
3. 补全之前，只要文字高度匹配也能被搜到。

## 启动

```sh
git clone https://github.com/Kling0012/hibi-memory.git
cd hibi-memory
cp .env.example .env   # 填写 LLM 和嵌入的接入点与密钥
docker compose up -d --build
./scripts/verify.sh    # 检查 /health 和 /status
```

### 主要配置（`.env`）

| 变量 | 说明 |
|---|---|
| `MEMORY_LLM_BASE_URL` / `MEMORY_LLM_API_KEY` / `MEMORY_LLM_MODEL` | 用于结构抽取的 OpenAI 兼容 API（如 GLM-5.3-Flash） |
| `MEMORY_EMBEDDING_MODE` | `local`（本地 llama-server）/ `cloudflare` / `cloudflare_primary_local_fallback` |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | 使用 Cloudflare Workers AI 时 |
| `MEMORY_EMBEDDING_DIM` | 嵌入维度（默认 1024。与 Hibi 同步服务器相同的 Qwen3-Embedding-0.6B） |
| `MEMORY_DEFAULT_USER` / `MEMORY_DEFAULT_PROJECT` | 未指定时的所有者和项目 |
| `MEMORY_SEARCH_RERANK_MODE` | `on` 时用 LLM 重排搜索结果（精度更高、更慢） |

`.env` 不要提交到 Git。

### 嵌入备用

仅当 Cloudflare 返回频率限制、超时、连接失败或 5xx 时，切换到本地 llama-server（`Qwen3-Embedding-0.6B` 的 GGUF），之后 300 秒内继续使用本地。配置错误（400 / 401 / 403）不会触发切换。llama-server 的 systemd 单元示例在 `systemd/`。

## AI 智能体的使用方式

### 注册

```sh
# Claude Code
claude mcp add --transport http hibi-memory http://<memory-host>:8377/mcp/
```

其他智能体也把 `http://<memory-host>:8377/mcp/` 注册为 MCP 的 HTTP 服务器。连接后会以 `memory://usage` 的形式提供使用方针。

### MCP 工具

| 工具 | 内容 |
|---|---|
| `memory_search` | 按语义和文字检索 |
| `memory_get` | 读取一条完整记录（含补全状态） |
| `memory_list` | 按期间列表（创建顺序，用游标继续取） |
| `memory_remember` | 保存需要长期保留的记录 |
| `memory_supersede` | 在保留历史的前提下，用新内容替换旧记录 |
| `session_put` / `session_get` / `session_search` | 工作中的备忘（默认 24 小时后消失） |
| `session_commit` | 把工作中已确定的部分升级为长期记录 |

也有删除工具（`memory_forget`），但运维中不使用，一律用替换（supersede）来修正。

### 值得记录的内容

- 使用者决定的事项、偏好、需要避免的做法
- 机器和服务器的构成、操作步骤
- 已查明原因的故障及其修法、试过但失败的方法
- 实验的条件和结果

密钥、密码、令牌、文件全文、大量日志请不要写入。

## REST API

| 方法 | 路径 | 内容 |
|---|---|---|
| GET | `/health` / `/status` | 状态与条数 |
| POST | `/v1/memories/search` | 搜索 |
| GET | `/v1/memories` | 按期间列表（`since` / `until` / `field` / `types` / `agent` / `limit`（最大 1000）/ `cursor`） |
| GET | `/v1/memories/{id}` | 一条记录 |
| POST | `/v1/memories` | 保存 |
| POST | `/v1/memories/{id}/supersede` | 替换 |
| POST | `/v1/sessions/put` / `/v1/sessions/commit` | 工作中的备忘与确定 |

详情请看 `http://<memory-host>:8377/docs` 的 OpenAPI。

{: .note }
在查询中写时刻时，`+09:00` 的 `+` 请写成 `%2B`。原样写会被当作空格，结果为 0 条。

## 与 Hibi 的关系

同步服务器每 5 分钟通过 `GET /v1/memories` 从 `AM_BASE_URL` 指定的共享记忆读取新记录（只读不写）。

- 一条记忆对应时间线上的一条“AI 的工作”。
- 泳道按写入的 AI 区分（如 `AI claude`、`AI codex`）。
- 被替换的旧记录在时间线中默认不显示。
- 正确的记录始终以共享记忆一侧为准。要修改时请在共享记忆中替换。

## 注意

- 没有用户认证。请只在封闭网络内使用。
- 只要知道记录 ID，任何人都可以读写。
- `docker compose down -v` 会连记忆一起删除，请勿使用。
