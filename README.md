<p align="center"><img src="frontend/build/icon.png" width="96" alt=""></p>

# Hibi

**日本語** · [English](README.en.md) · [中文](README.zh.md)

自分が PC でしたことと、AI エージェントがしたことを、ひとつのタイムラインにまとめる個人用の作業記録アプリです。PC の画面から「いま何をしているか」を AI が読み取って記録し、その日の記録から日報とブログを書きます。

**ドキュメント: https://kling0012.github.io/hibi/**

> **このプロジェクトは [volcengine/MineContext](https://github.com/volcengine/MineContext) をもとにしています。**
> 元のプロジェクトは Apache License 2.0 で公開されており、このリポジトリも同じライセンスに従います（[LICENSE](LICENSE)）。
> 名前、画面、AI の接続方式、記録のしくみを大きく変えた別のアプリです。元の README は [docs/upstream/](docs/upstream/README.md) にあります。

## Hibi を構成する 3 つのリポジトリ

| リポジトリ | 役割 |
|---|---|
| **hibi**（ここ） | デスクトップアプリ（Apple シリコンの Mac / Windows）。画面の記録、作業の宣言、閲覧 |
| [hibi-server](https://github.com/Kling0012/hibi-server) | 同期サーバ。記録の集約、検索、作業終了の判定、日報・ブログ、スマホ用の画面 |
| [hibi-memory](https://github.com/Kling0012/hibi-memory) | AI の共有記憶。AI エージェントの作業記録の置き場 |

## できること

- **画面の記録** — 画面を 1 秒に 1 枚まで AI に送り、30 秒ごとの活動の要約と、画面が変わったときの正確な書き起こしを残します。パスワード管理アプリなどは除外できます。
- **AI の自動切り替え** — Gemini Live（無料枠）を優先し、上限や障害のときは GLM などの OpenAI 互換 API へ自動で切り替えます。
- **作業の宣言** — 「これから○○をする」と書いて始めると、終わりは AI が画面の様子から判断して作業時間を残します。
- **タイムライン** — 作業・端末・AI ごとのレーンで 1 日を並べ、1 か月のヒートマップで振り返ります。
- **日報とブログ** — 毎晩、その日の記録から AI が日報を書き、ブログの下書きを作ります。Claude Code などに MCP で書かせることもできます。
- **どこからでも** — 記録は自分の同期サーバに集まり、別の PC やスマホ・iPad からも同じ記録を見られます。

## はじめかた

1. 同期サーバを立てる（[hibi-server](https://github.com/Kling0012/hibi-server)）
2. [Releases](https://github.com/Kling0012/hibi/releases) からアプリを入れる
   - macOS は署名していないため、初回だけ `xattr -dr com.apple.quarantine /Applications/Hibi.app` を実行
3. 初回の設定画面で AI の接続先と同期サーバの URL を入れる

詳しくは [セットアップ](https://kling0012.github.io/hibi/getting-started.html) を見てください。

## 開発

必要なもの: Python 3.12 と [uv](https://docs.astral.sh/uv/)、Node.js 22 以上と pnpm 10。

```sh
uv sync
uv run python -m opencontext.cli start   # Python バックエンド（127.0.0.1:1733）
cd frontend && pnpm install && pnpm run dev
```

| ディレクトリ | 中身 |
|---|---|
| `frontend/` | Electron 44 + React 19 のアプリ本体 |
| `opencontext/` | 同梱する Python バックエンド（画面の AI 要約、ToDo・ヒント、同期サーバへの送信） |
| `config/` | 既定の設定（`config.yaml`） |
| `docs/` | ドキュメントサイト（GitHub Pages） |

配布用のビルドは、Python 部分を PyInstaller で固めるため OS ごとにその OS 上で行います。GitHub Actions の `release.yml` を手動で動かすと、macOS（Apple シリコン）版と Windows 版を下書きのリリースに添付します。

```sh
gh workflow run release.yml -R Kling0012/hibi -f tag=v0.1.0
```

## 元のプロジェクトからの主な変更

- 名前とアイコンを Hibi に変更（以前の MineContext のデータは初回起動時に引き継ぎ）
- 画面を日本語化し、サイドバー・ホーム・設定を作り直し
- AI を複数の接続先から用途ごとに選び、失敗時に自動で切り替える方式に変更（Gemini Live、OpenAI 互換）
- 画面の記録を、Live API への連続送信と、変化時の正確な書き起こしの 2 段に変更
- 同期サーバへの送信、作業の宣言、同期サーバの画面（タイムライン・日報・ブログ・検索）の表示を追加
- フォルダ・文書・Web ページの取り込み、Doubao / Ark、補完機能、旧 Web 画面、スクリーンショット解析とアプリ内のベクトル検索（chromadb）を削除
- Electron 44、React 19、TypeScript 7、Python 3.12 / uv に更新

## ライセンス

Apache License 2.0。元のプロジェクトの著作権表示は各ファイルと [LICENSE](LICENSE) に残しています。
