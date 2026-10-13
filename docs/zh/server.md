---
title: 同步服务器
parent: 中文
nav_order: 5
---

[日本語](../server.html) · [English](../en/server.html) · **中文**

# 同步服务器（hibi-server）
{: .no_toc }

收集所有设备的记录与 AI 的工作，负责时间线、搜索、工作结束判定、日报、博客的服务器。仓库: [Kling0012/hibi-server](https://github.com/Kling0012/hibi-server)
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## 构成

| 组件 | 内容 |
|---|---|
| API | Python 3.12、FastAPI、SQLAlchemy 2、psycopg 3、Alembic |
| 数据库 | PostgreSQL 17 + pgvector + pg_trgm（`pgvector/pgvector:pg17`） |
| 界面 | `/ui/`（管理界面）、`/blog/`（博客）。`/` 跳转到 `/ui/` |
| 启动 | Docker Compose（`db` 与 `api`）。`api` 启动时自动把数据库迁移到最新结构 |

小机器即可（生产上限: API 512 MB、数据库 768 MB）。

### 后台任务

| 任务 | 周期 | 内容 |
|---|---|---|
| 嵌入 | 5 秒（生产 10 秒） | 批量嵌入尚无嵌入的记录 |
| 工作结束判定 | 120 秒（生产 300 秒） | 让 LLM 判断进行中的工作是否结束。离开 30 分钟以上也会结束 |
| 待办时间计入 | 300 秒 | 查看新记录，让 LLM 判断你在处理哪项待办，并累加时间 |
| AI 工作导入 | 300 秒 | 从共享记忆导入新记录 |
| 日报 | 每天 23:55 / 次日 6:00 | 撰写当天日报，次日早晨重写 |
| 博客 | 日报之后 | 按配置起草文章（[日报与博客](blog.html)） |

失败时只是跳过该轮，记录接收和搜索不会中断。

## 启动

### 开发用（不使用外部 API）

```sh
docker compose up -d --build
curl -s http://127.0.0.1:18741/healthz
```

嵌入和 LLM 变成不与外部通信的确定性假实现（`fake`）。

### 生产

叠加 `docker-compose.prod.yml` 启动。配置放在 `.env`，密钥放在 `secrets/`（[安装设置](getting-started.html#以生产模式运行)）。

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p hibi up -d --build
```

生产叠加层带来的变化:

- 只监听 `PROD_TAILNET_IP:PROD_PORT` 和 `127.0.0.1:PROD_PORT`
- 嵌入改用 Cloudflare Workers AI（`@cf/qwen/qwen3-embedding-0.6b`，1024 维）
- LLM 指向 `.env` 的 `LLM_BASE_URL`
- `secrets/` 的密钥以只读方式挂载进容器
- 内存上限与自动重启（`restart: unless-stopped`）

{: .warning }
服务器没有用户认证。请不要监听 `0.0.0.0` 或公网 IP。

## 环境变量

### 嵌入

| 变量 | 默认 | 说明 |
|---|---|---|
| `EMBED_PROVIDER` | `fake` | `fake` / `openai_compat` / `cloudflare` |
| `EMBED_BASE_URL` | — | `openai_compat` 的 base URL（到 `.../v1`） |
| `EMBED_MODEL` | `fake-embed` | 模型名。cloudflare 为 `@cf/qwen/qwen3-embedding-0.6b` |
| `EMBED_DIM` | `1024` | 维度。**首次启动时固定到数据库**。修改需重建数据库 |
| `EMBED_API_KEY_FILE` | — | `openai_compat` 的密钥文件 |
| `EMBED_CF_ENV_FILE` | — | 写有 `CLOUDFLARE_ACCOUNT_ID` 和 `CLOUDFLARE_API_TOKEN` 的文件 |
| `EMBED_BATCH` | `64` | 每次批处理的条数（Workers AI 会拒绝过大批次，生产用 32） |
| `EMBED_INTERVAL_SEC` | `5` | 周期（秒） |
| `EMBED_RELAY_MAX_INPUTS` | `64` | 嵌入中继（`POST /v1/embeddings`）单次接受的条数 |

### LLM（工作结束判定、日报、博客）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `openai_compat` | `openai_compat` / `fake` |
| `LLM_BASE_URL` | — | OpenAI 兼容 base URL。留空则不执行判定、日报、博客 |
| `LLM_API_KEY_FILE` | — | 密钥文件 |
| `LLM_MODEL` | `glm-5.3-flash` | 工作结束判定所用模型 |
| `LLM_TIMEOUT_SEC` | `30` | 超时（秒） |
| `SESSION_JUDGE_INTERVAL` | `120` | 判定周期（秒） |
| `SESSION_END_THRESHOLD` | `0.75` | 置信度达到该值以上即视为“已结束” |
| `IDLE_MINUTES` | `15` | 所有设备这么多分钟没有记录即结束 |
| `REPORT_LLM_MODEL` | `glm-5.3` | 撰写日报的模型 |
| `REPORT_LLM_FLASH_MODEL` | `glm-5.3-flash` | 材料过长时用于预总结的模型 |
| `REPORT_LLM_TIMEOUT_SEC` | `180` | 日报超时（秒） |
| `REPORT_CHUNK_CHARS` | `12000` | 材料超过该长度时先按 4 小时分块预总结再写 |
| `REPORT_AUTO_ENABLED` | `1` | 自动生成日报 |
| `BLOG_AUTO` | `draft` | 自动写博客。`off` / `draft` / `publish`（[日报与博客](blog.html)） |

### AI 工作导入

| 变量 | 默认 | 说明 |
|---|---|---|
| `AM_BASE_URL` | — | 共享记忆的 URL（如 `http://<memory-host>:8377`）。留空则不导入 |
| `AM_IMPORT_ENABLED` | `1` | 启用/禁用导入 |
| `AM_IMPORT_INTERVAL` | `300` | 周期（秒） |
| `AM_IMPORT_LOOKBACK_HOURS` | `48` | 首次回溯的小时数 |
| `AM_TIMEOUT_SEC` | `15` | 超时（秒） |

### 其他

| 变量 | 默认 | 说明 |
|---|---|---|
| `TZ_DEFAULT` | `Asia/Tokyo` | 用于日期划分的时区 |
| `API_PORT` | `18741` | 开发用监听端口 |
| `PROD_TAILNET_IP` / `PROD_PORT` | — / `18740` | 生产监听地址 |
| `SESSION_AWAY_END_MINUTES` | `30` | 工作期间离开达到该分钟数时，以离开时刻作为工作结束时间 |
| `TODO_JUDGE_INTERVAL` | `300` | 待办时间计入的运行间隔（秒） |
| `TODO_MATCH_THRESHOLD` | `0.7` | 判定正在处理某项待办的最低置信度 |
| `TODO_JUDGE_MAX_WINDOW_MIN` | `30` | 每次查看记录的时间窗口（分钟） |
| `TODO_MERGE_GAP_MIN` | `5` | 与上次计入相隔不超过该分钟数时，延长同一条带 |
| `MCP_ALLOWED_HOSTS` | — | MCP（`/mcp`）接受的 Host（如 `100.x.y.z:18740`，逗号分隔）。留空则不校验 Host |

## 数据

| 表 | 内容 |
|---|---|
| `devices` | 设备（名称、系统、最后在线时间） |
| `events` | 记录。种类为 `activity` / `screen_summary` / `note` / `todo` / `ai_work` / `report`。ID 为设备生成的 UUID，相同 ID 会覆盖 |
| `event_embeddings` | 记录的嵌入。没有嵌入的记录在队列中等待 |
| `work_sessions` | 已声明的工作。同时只能有 1 条进行中。结束方式为 `manual` / `ai_detected` / `idle` / `superseded` |
| `blog_posts` | 博客文章 |
| `importer_state` | 导入进度 |

时刻带时区接收，以 UTC 返回。

## 搜索原理

`POST /v1/search` 合并两种搜索。

1. 嵌入向量最相近的前 50 条（pgvector，余弦距离）
2. 字符串最相似的前 50 条（pg_trgm）

二者按名次融合（Reciprocal Rank Fusion，k=60），附带分数和依据返回。尚未嵌入的记录也能被第 2 种搜到。

## 运维

### 更新

```sh
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p hibi up -d --build api
```

界面文件打包进镜像，因此只改界面也需要 `--build`。

### 备份

`scripts/backup.sh` 用 `pg_dump -Fc` 把数据库保存到 `backups/`，并删除超过 14 天的旧备份。请用 systemd timer 或 cron 每天运行。

```sh
# 查看内容
docker exec -i hibi-db-1 pg_restore -l < backups/hibi-YYYYMMDD-HHMM.dump
```

恢复（会重建数据库，请注意）:

```sh
docker compose -p hibi stop api
docker exec -i hibi-db-1 psql -U sync -d postgres -c 'DROP DATABASE sync' -c 'CREATE DATABASE sync OWNER sync'
docker exec -i hibi-db-1 pg_restore -U sync --no-owner -d sync < backups/hibi-YYYYMMDD-HHMM.dump
docker compose -p hibi start api
```

`docker compose down -v` 会连数据库卷一起删除，请勿使用。

### 测试

```sh
sudo ./scripts/e2e.sh
```

以另一个项目名临时启动，用假嵌入和 LLM 全面验证 API 后清理环境。

## 注意

- 容器访问不到共享记忆时，请确认主机防火墙（ufw 等）没有拦截来自容器的通信。
- 开发用数据库的用户名和密码是固定值。数据库端口没有对外开放。
- AI 结束的工作保持未确认状态。请在管理界面确认或修改。
