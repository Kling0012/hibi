# MineContext（Kling 版）

自分専用の作業記録アプリです。PC の画面から「いま何をしているか」を AI が読み取って記録し、その日の作業を時系列にまとめます。Mac と Windows の記録を一か所に集め、AI エージェントの作業記録も同じタイムラインに並べて、日報や備忘録ブログを作ります。

> **このプロジェクトは [volcengine/MineContext](https://github.com/volcengine/MineContext) を元にしています。**
> 元のプロジェクトは Apache License 2.0 で公開されており、このリポジトリも同じライセンスに従います（[LICENSE](LICENSE)）。
> 元の README は [docs/upstream/](docs/upstream/README.md) に残しています。元のプロジェクトをそのまま追う場合は、このリポジトリの `main` ブランチ（フォーク元と同じ状態）を使ってください。
> 本リポジトリでの主な変更点は下の「元のプロジェクトからの変更」にまとめています。

## できること

| 機能 | 状態 |
|---|---|
| 画面の記録と AI による要約（Electron デスクトップアプリ） | 元のプロジェクトの機能を刷新中 |
| AI の接続先を優先順位付きで切り替え（Gemini Live → GLM-5.3 Flash など、上限に達したら自動で次へ） | 開発中 |
| 複数端末の記録を集める同期サーバ（PostgreSQL + pgvector、日本語の意味検索） | 実装済み・配置作業中 |
| 「これから○○をする」と作業を宣言し、終わりは AI が画面の様子から判断して作業時間を記録 | 実装済み（サーバ側） |
| AI エージェントの作業記録（共有記憶 agent-memory）を同じタイムラインに取り込み | 開発中 |
| スマホ・iPad から使う管理画面 | 予定 |
| その日の記録から備忘録ブログを自動で書く | 予定 |

## 構成

```
 Mac / Windows                         同期サーバ（tailnet 内）            agent-memory
┌──────────────────────┐   作業記録   ┌───────────────────────────┐   読み取り   ┌──────────────┐
│ デスクトップアプリ    │ ───────────▶ │ sync-server               │ ◀─────────── │ AI の共有記憶 │
│  frontend/ (Electron) │              │  FastAPI + PostgreSQL      │              └──────────────┘
│  opencontext/ (Python)│              │  + pgvector                │
│   画面の撮影と AI 要約 │              │  タイムライン・検索・作業時間 │ ──▶ 日報・ブログ（予定）
└──────────────────────┘              └───────────────────────────┘
         │                                     │
         ▼                                     ▼
  Gemini Live / GLM-5.3 Flash          埋め込み: Cloudflare Workers AI
  （画面の要約）                        （Qwen3-Embedding-0.6B）
```

- `frontend/` … Electron のデスクトップアプリ（画面の撮影、設定、チャット）
- `opencontext/` … アプリに同梱する Python のバックエンド（AI による要約、日報、Todo、Tips）
- `sync-server/` … 全端末の記録を集めるサーバ。使い方は [sync-server/README.md](sync-server/README.md)

## ビルド

配布用のアプリは OS ごとにその OS 上でビルドする必要があります（Python 部分を PyInstaller でまとめるため、クロスビルドできません）。手元に Mac がなくても、GitHub Actions（`.github/workflows/release.yml`）で macOS・Windows・Linux 版を作れます。

手元で開発する場合:

```sh
# Python バックエンド（Python 3.12、uv）
uv sync
uv run python -m opencontext.cli start

# デスクトップアプリ（Node 22、pnpm 10）
cd frontend
pnpm install
pnpm run dev
```

macOS 版は署名していないため、初回は次のコマンドで Gatekeeper の隔離属性を外してください。

```sh
xattr -dr com.apple.quarantine /Applications/MineContext.app
```

## 元のプロジェクトからの変更

- Python 3.12 に更新し、依存関係を `uv.lock` で固定
- Electron 44、TypeScript 7 などフロントエンドの依存関係を更新
- 配布先・自動更新を本リポジトリ向けに変更（自動更新は既定でオフ）
- 使わない機能を削除: volcengine Ark / doubao、入力補完、フォルダ・文書・Web リンクの取り込みと Web 検索、Python 側の画面撮影、qdrant、旧 HTML 画面
- 同期サーバ `sync-server/` と作業宣言・作業時間の自動記録を追加

## ライセンス

Apache License 2.0。元のプロジェクト [volcengine/MineContext](https://github.com/volcengine/MineContext) の著作権表示とライセンスを引き継いでいます。本リポジトリで追加・変更した部分も同じライセンスで公開します。
