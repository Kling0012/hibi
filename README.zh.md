<p align="center"><img src="frontend/build/icon.png" width="96" alt=""></p>

# Hibi

**日本語** ([README.md](README.md)) · **English** ([README.en.md](README.en.md)) · **中文**（本文件）

一个个人工作记录应用，把你在电脑上做的事和 AI 智能体做的事汇成同一条时间线。AI 读取电脑屏幕记录“现在正在做什么”，并根据当天的记录撰写日报和博客文章。

**文档: https://kling0012.github.io/hibi/zh/**

> **本项目基于 [volcengine/MineContext](https://github.com/volcengine/MineContext)。**
> 原项目以 Apache License 2.0 发布，本仓库遵循同一许可（[LICENSE](LICENSE)）。
> 本项目在名称、界面、AI 接入方式、记录机制上都有大幅改动，是另一个应用。原 README 保留在 [docs/upstream/](docs/upstream/README.md)。

## 组成 Hibi 的三个仓库

| 仓库 | 职责 |
|---|---|
| **hibi**（本仓库） | 桌面应用（Apple 芯片 Mac / Windows）。屏幕记录、声明工作、查看 |
| [hibi-server](https://github.com/Kling0012/hibi-server) | 同步服务器。记录汇集、搜索、工作结束判定、日报/博客、手机用界面 |
| [hibi-memory](https://github.com/Kling0012/hibi-memory) | AI 共享记忆。AI 智能体工作记录的存放处 |

## 功能一览

- **屏幕记录** — 以每秒最多 1 帧把屏幕发给 AI，保留每 30 秒的活动摘要和画面变化时的精确转写。密码管理器等应用可以排除。
- **AI 自动切换** — 优先使用 Gemini Live（免费额度），达到上限或故障时自动切换到 GLM 等 OpenAI 兼容 API。
- **声明工作** — 写下“接下来要做○○”即可开始，结束由 AI 根据屏幕情形判断，工作时间因此被保留。
- **时间线** — 按工作、设备、AI 分泳道排列一天，用一个月的热力图回顾。
- **日报与博客** — 每晚 AI 根据当天记录撰写日报并起草博客。也可以让 Claude Code 等通过 MCP 撰写。
- **随处可看** — 记录汇集到自己的同步服务器，从另一台电脑、手机或 iPad 都能看到同一份记录。

## 开始使用

1. 搭建同步服务器（[hibi-server](https://github.com/Kling0012/hibi-server)）
2. 从 [Releases](https://github.com/Kling0012/hibi/releases) 安装应用
   - macOS 版未签名，首次需执行一次 `xattr -dr com.apple.quarantine /Applications/Hibi.app`
3. 在首次的设置界面里填写 AI 接入点和同步服务器 URL

详见[安装设置](https://kling0012.github.io/hibi/zh/getting-started.html)。

## 开发

需要: Python 3.12 和 [uv](https://docs.astral.sh/uv/)、Node.js 22 以上和 pnpm 10。

```sh
uv sync
uv run python -m opencontext.cli start   # Python 后端（127.0.0.1:1733）
cd frontend && pnpm install && pnpm run dev
```

| 目录 | 内容 |
|---|---|
| `frontend/` | Electron 44 + React 19 的应用本体 |
| `opencontext/` | 随应用打包的 Python 后端（屏幕 AI 摘要、待办/提示、发送到同步服务器） |
| `config/` | 默认设置（`config.yaml`） |
| `docs/` | 文档站（GitHub Pages） |

发布构建因 Python 部分用 PyInstaller 打包，须在各系统上分别进行。手动运行 GitHub Actions 的 `release.yml` 会构建 macOS（Apple 芯片）版和 Windows 版，并附到草稿 release 上。

```sh
gh workflow run release.yml -R Kling0012/hibi -f tag=v0.1.0
```

## 相对原项目的主要变更

- 更名为 Hibi 并更换图标（原有 MineContext 数据在首次启动时迁移）
- 界面日文化，重构侧边栏、主界面和设置
- AI 改为从多个接入点按用途选择、失败时自动切换的方式（Gemini Live、OpenAI 兼容）
- 屏幕记录改为两段式: 向 Live API 持续推流 + 变化时的精确转写
- 新增发送到同步服务器、声明工作、显示同步服务器界面（时间线、日报、博客、搜索）
- 删除文件夹/文档/网页导入、Doubao / Ark、补全功能、旧 Web 界面、截图解析和应用内向量搜索（chromadb）
- 更新到 Electron 44、React 19、TypeScript 7、Python 3.12 / uv

## 许可

Apache License 2.0。原项目的著作权声明保留在各文件和 [LICENSE](LICENSE) 中。
