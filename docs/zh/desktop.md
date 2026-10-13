---
title: 桌面应用
parent: 中文
nav_order: 4
---

[日本語](../desktop.html) · [English](../en/desktop.html) · **中文**

# 桌面应用（hibi）
{: .no_toc }

在 Apple 芯片 Mac 和 Windows 上运行的应用。录制屏幕并让 AI 总结，然后发送到同步服务器。仓库: [Kling0012/hibi](https://github.com/Kling0012/hibi)
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## 结构原理

应用由两部分组成。

| 部分 | 内容 | 职责 |
|---|---|---|
| `frontend/` | Electron + React | 屏幕拍摄、声明工作、界面显示、设置 |
| `opencontext/` | Python（FastAPI），随应用打包 | AI 摘要、待办/提示、发送到同步服务器 |

Python 端只在 `127.0.0.1:1733` 监听，外部无法访问。

```
屏幕（每秒最多 1 帧）+ 最前台应用名、窗口标题、URL
   │
   ▼
ScreenLiveMonitor（Python）
   ├─ 属于不记录的应用则丢弃
   ├─ 持续流向 Gemini Live，每 30 秒总结“正在做什么” → activity
   └─ 窗口切换或画面大幅变化时，用 GLM 准确记录屏幕文字 → screen_summary
   │
   ▼
本地保存 + 发送队列（outbox）──▶ 同步服务器（批量发送；失败时记录仍继续）
```

屏幕图像本身不会发送到服务器。发送的只有摘要和元信息。

## 界面

| 侧边栏 | 内容 |
|---|---|
| 主界面 | 声明工作、今日数字、今日泳道、待办、提示、与 AI 对话 |
| 时间线 | 同步服务器的时间线（泳道、列表、热力图） |
| 日报 | 同步服务器的日报 |
| 博客 | 同步服务器的博客 |
| 搜索 | 自己的记录与 AI 工作的搜索 |
| 屏幕记录 | 开始/停止记录、记录对象、设备内记录列表 |
| 设置 | AI / 屏幕记录 / 同步 / 应用 |

时间线、日报、博客、搜索是把同步服务器的界面直接嵌入应用显示，与手机上看到的相同。连不上同步服务器时，会显示相应提示和跳转设置的按钮。

## AI 接入点

在**设置 → AI** 里配置要使用的 AI（接入点）以及各用途的优先顺序。

### 接入点

| 项目 | 说明 |
|---|---|
| ID | 任意名称。用于用途排序 |
| 类型 | `gemini_live`（Google 的 Live API）或 `openai_compat`（OpenAI 兼容 API） |
| 模型 | 如 `gemini-3.8-live`、`glm-5.3-flash` |
| Base URL | `openai_compat` 时填写。如 `https://open.bigmodel.cn/api/coding/paas/v4` |
| API 密钥 | 文件路径（可用 `~`）或直接输入。直接输入的值不会再次显示 |

### 用途

| 用途 | 使用场景 | 默认顺序 |
|---|---|---|
| 图像理解（vision） | 屏幕摘要 | Gemini Live → GLM-5.3-Flash |
| 对话（chat） | “与 AI 对话” | GLM-5.3-Flash → GLM-5.3 |
| 结构化输出（structured） | 待办、提示的 JSON 输出 | GLM-5.3-Flash |
| 精确记录（exact） | 屏幕文字转写 | GLM-5.3-Flash |

按顺序从前往后使用，失败时切换到下一个。触发切换的失败有: 频率限制（429）、超额、服务器错误（5xx）、超时、断连、模型不匹配。失败的接入点会休息一段时间（有 `Retry-After` 时按其秒数，否则从 60 秒起翻倍，最长 15 分钟）。

把某个用途留空，只有用到该用途的功能会停止，其余照常运行。

应用本身不执行搜索，而是使用同步服务器的混合搜索（对话中的搜索也一样）。因此 embedding 用途默认为空。

### 关于 Gemini Live

Gemini Live（`gemini-3.8-live`）免费额度大，适合持续推流屏幕。每秒 1 帧推流大约消耗每分钟 8,000 token。

- 每个用途保持 1 条连接，断开后自动重连（Live 连接有时在 40–45 秒左右悄然断开）。
- 响应以语音返回，使用其转写文本作为文字。
- GLM-5.3-Flash 的文字准确度更高，因此屏幕文字的精确记录（exact）使用 GLM。

## 屏幕记录

**设置 → 屏幕记录**

| 项目 | 默认 | 说明 |
|---|---|---|
| 暂停记录 | 关 | 开启期间完全不截取画面 |
| 直播帧率 | 1 | 每秒帧数（0.2–1） |
| 直播摘要间隔 | 30 秒 | 将推流画面汇总为活动的间隔 |
| 文字记录最小间隔 | 20 秒 | 屏幕文字转写间隔的下限 |
| 变化阈值 | 10 | 画面变化多大才触发转写（dhash 距离，越大越迟钝） |
| 不记录的应用 | 1Password, Bitwarden, KeePassXC | 见下 |
| 多久无操作视为离开 | 5 分钟 | 这么久没有操作就视为离开，停止记录（3 / 5 / 10 / 15 分钟，或禁用） |

### 离开

应用每 15 秒查看操作系统的“最后操作时刻”。超过阈值后视为离开，停止屏幕记录，回来后恢复。锁屏和睡眠会立即视为离开。如果此前是手动暂停记录，回来后也不会恢复。

离开 1 分钟以上，会以 `kind=away` 记录发送到同步服务器（`meta.reason` 为 `idle` / `lock` / `suspend`）。离开期间，主界面和“屏幕记录”会显示“离开中（从 14:05 起）”。

### 不记录的应用

最前台应用名包含此处填写的字符串时，该画面即被丢弃（也不会发给 AI）。请把密码管理器、银行、私人聊天等写进去。每行一条。

## 同步

**设置 → 同步**

| 项目 | 说明 |
|---|---|
| 发送到同步服务器 | 关闭时记录仍攒在本地，打开后即发送 |
| 服务器 URL | 如 `http://100.x.y.z:18740` |
| 设备名 | 时间线中的泳道名。留空则用主机名 |

下方显示设备 ID、最后发送时间、未发送条数和最近的错误。每次发送最多 100 条，失败时间隔从 5 秒延长到 60 秒重试。同一条记录发送两次也不会重复。

## 待办与提示

主界面的“待办”嵌入同步服务器的界面。待办由你自己添加，投入的时间由同步服务器根据记录自动累加（[使用方法](usage.html#待办)）。AI 不会自行创建待办。连不上同步服务器时，待办无法使用。

“提示”（每 1 小时，最多 2 条）是 AI 根据屏幕记录（转写和活动摘要）以及同步服务器上当天的工作生成的提醒。例如反复看到同一个错误、不间断地持续工作、画面与声明的工作偏离等。没有依据就不会显示。24 小时内已提示过的相同内容不会重复。疑似密码、令牌、邮箱地址的字符串不会写入提示。

## 显示语言

**设置 → 应用 →“显示语言”**（自动 / 日语 / English / 中文）。修改后立即切换，托盘菜单也使用相同语言。嵌入的同步服务器界面也会收到同一语言设置。

## 自动更新

**设置 → 应用 →“自动检查更新”**（默认开启）。启动 30 秒后以及之后每 6 小时检查 GitHub 的 release。

| 系统 | 行为 |
|---|---|
| Windows | 后台下载，侧边栏下方出现“重启以更新” |
| Mac | 未签名，不会自动替换。点“有新版本”会打开下载页面 |

使用开发版（`-dev.N`）期间，也会收到开发版的更新。

## 设置文件

在界面中修改的值保存在应用数据目录的 `config/user_setting.yaml`。默认值来自随应用打包的 `config/config.yaml`。

| 系统 | 数据目录 |
|---|---|
| macOS | `~/Library/Application Support/Hibi` |
| Windows | `%APPDATA%\Hibi` |

API 密钥文件的默认位置是 `~/.config/minecontext/gemini-key` 和 `~/.config/minecontext/zhipu-key`（可用环境变量 `GEMINI_KEY_FILE` / `ZHIPU_KEY_FILE` 更改）。

## 开发

```sh
git clone https://github.com/Kling0012/hibi.git
cd hibi
uv sync                                  # Python 3.12
uv run python -m opencontext.cli start   # 后端
cd frontend && pnpm install && pnpm run dev
```

需要: Python 3.12 和 [uv](https://docs.astral.sh/uv/)、Node.js 22 以上和 pnpm 10。

### 发布构建

Python 部分用 PyInstaller 打包，因此**各系统的构建须在该系统上进行**。手动运行 GitHub Actions 的 `release.yml` 会构建 macOS（Apple 芯片）版和 Windows 版，并附到草稿 release 上。

```sh
gh workflow run release.yml -R Kling0012/hibi -f tag=v0.1.0
```

本地构建则在 `frontend/` 里执行 `pnpm run build:mac` / `build:win`。只支持 Apple 芯片 Mac 和 Windows。

## 与原项目的差异

相对于 [volcengine/MineContext](https://github.com/volcengine/MineContext) 做了如下更改。

- 更名为 Hibi 并更换图标（原有 MineContext 数据在首次启动时迁移）
- AI 改为从多个接入点按用途选择、失败时自动切换的方式（Gemini Live、OpenAI 兼容）
- 屏幕记录改为两段式: 向 Live API 持续推流 + 变化时的精确转写
- 新增发送到同步服务器、声明工作、显示同步服务器界面
- 删除文件夹/文档/网页导入、Doubao / Ark、补全功能、旧 Web 界面
- 界面日文化，重构侧边栏和主界面
- 更新到 Electron 44、React 19、Python 3.12 / uv
