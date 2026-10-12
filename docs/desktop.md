---
title: デスクトップアプリ
nav_order: 4
---

# デスクトップアプリ（hibi）
{: .no_toc }

Mac / Windows で動くアプリです。画面を記録して AI に要約させ、同期サーバへ送ります。リポジトリ: [Kling0012/hibi](https://github.com/Kling0012/hibi)
{: .fs-5 .fw-300 }

1. TOC
{:toc}

## しくみ

アプリは 2 つの部分でできています。

| 部分 | 中身 | 役割 |
|---|---|---|
| `frontend/` | Electron + React | 画面の撮影、作業の宣言、画面表示、設定 |
| `opencontext/` | Python（FastAPI）。アプリに同梱 | AI による要約、ToDo・ヒント、同期サーバへの送信 |

Python 側は `127.0.0.1:1733` だけで待ち受け、外からは届きません。

```
画面（最大 1 枚/秒）＋ 最前面のアプリ名・ウィンドウ名・URL
   │
   ▼
ScreenLiveMonitor（Python）
   ├─ 記録しないアプリなら捨てる
   ├─ Gemini Live に流し続け、30 秒ごとに「何をしているか」を要約 → activity
   └─ ウィンドウが変わった・画面が大きく変わったとき、GLM で画面の文字を正確に記録 → screen_summary
   │
   ▼
ローカル保存 ＋ 送信キュー（outbox）──▶ 同期サーバ（まとめて送信、失敗しても記録は続く）
```

画面の画像そのものはサーバへ送りません。送るのは要約とメタ情報です。

## 画面

| サイドバー | 中身 |
|---|---|
| ホーム | 作業の宣言、今日の数字、今日のレーン、ToDo、ヒント、AI と話す |
| タイムライン | 同期サーバのタイムライン（レーン、一覧、ヒートマップ） |
| 日報 | 同期サーバの日報 |
| ブログ | 同期サーバのブログ |
| 検索 | 自分の記録と AI の作業の検索 |
| 画面の記録 | 記録の開始・停止、記録の対象、端末内の記録の一覧 |
| 設定 | AI / 画面の記録 / 同期 / アプリ |

タイムライン・日報・ブログ・検索は、同期サーバの画面をそのままアプリ内に表示しています。スマホで見るものと同じです。同期サーバにつながらないときは、その旨と設定へのボタンが出ます。

## AI の接続先

**設定 → AI** で、使う AI（接続先）と、用途ごとの優先順を決めます。

### 接続先

| 項目 | 説明 |
|---|---|
| ID | 自由な名前。用途の並びで使う |
| 種類 | `gemini_live`（Google の Live API）または `openai_compat`（OpenAI 互換 API） |
| モデル | 例: `gemini-3.8-live`、`glm-5.3-flash` |
| ベース URL | `openai_compat` のとき。例: `https://open.bigmodel.cn/api/coding/paas/v4` |
| API キー | ファイルのパス（`~` 可）か直接入力。直接入力した値は画面に再表示されません |

### 用途

| 用途 | 使われる場面 | 既定の並び |
|---|---|---|
| 画像の理解（vision） | 画面の要約 | Gemini Live → GLM-5.3-Flash |
| チャット（chat） | 「AI と話す」 | GLM-5.3-Flash → GLM-5.3 |
| 構造化出力（structured） | ToDo などの JSON 出力 | GLM-5.3-Flash |
| 埋め込み（embedding） | 端末内の検索 | 同期サーバの中継（キー不要） |
| 正確な記録（exact） | 画面の文字の書き起こし | GLM-5.3-Flash |

並びの先頭から使い、失敗したら次に切り替えます。切り替わる失敗は、回数制限（429）、上限超過、サーバ側のエラー（5xx）、時間切れ、切断、モデルの不一致です。失敗した接続先は少し休ませます（`Retry-After` があればその秒数、なければ 60 秒から倍々で最大 15 分）。

用途を空にすると、その用途を使う機能だけが止まり、ほかは動き続けます。

### Gemini Live について

Gemini Live（`gemini-3.8-live`）は無料枠が大きく、画面を流し続ける使い方に向いています。1 秒 1 枚で流して 1 分あたりおよそ 8,000 トークンです。

- 接続は用途ごとに 1 本保ち、途切れたら自動でつなぎ直します（Live の接続は 40〜45 秒ほどで黙って切れることがあります）。
- 応答は音声で返り、その書き起こしを文字として使います。
- 文字の正確さは GLM-5.3-Flash のほうが上なので、画面の文字の記録（exact）は GLM を使います。

## 画面の記録

**設定 → 画面の記録**

| 項目 | 既定 | 説明 |
|---|---|---|
| 記録を一時停止 | オフ | オンの間は画面を一切取得しません |
| ライブのフレームレート | 1 | 1 秒あたりの枚数（0.2〜1） |
| ライブ要約の間隔 | 30 秒 | 流れている画面を活動としてまとめる間隔 |
| 文字記録の最小間隔 | 20 秒 | 画面の文字を書き起こす間隔の下限 |
| 変化のしきい値 | 10 | 画面がどれだけ変わったら書き起こすか（dhash 距離。大きいほど鈍い） |
| 記録しないアプリ | 1Password, Bitwarden, KeePassXC | 下記 |

### 記録しないアプリ

最前面のアプリ名に、ここに書いた文字が含まれていると、その画面は捨てます（AI にも送りません）。パスワード管理アプリ、銀行、個人的なチャットなどを書いておいてください。1 行に 1 つです。

## 同期

**設定 → 同期**

| 項目 | 説明 |
|---|---|
| 同期サーバへ送る | オフでも記録は手元に貯まり、オンにすると送られます |
| サーバの URL | 例: `http://100.x.y.z:18740` |
| 端末名 | タイムラインのレーン名。空ならホスト名 |

下に端末 ID、最後に送った時刻、未送信の件数、直近のエラーが出ます。送信は 1 回 100 件まで、失敗したら 5 秒から 60 秒まで間隔を延ばして送り直します。同じ記録を 2 回送っても重複しません。

## 設定ファイル

画面で変えた値は、アプリのデータフォルダの `config/user_setting.yaml` に保存されます。既定値はアプリに同梱の `config/config.yaml` です。

| OS | データフォルダ |
|---|---|
| macOS | `~/Library/Application Support/Hibi` |
| Windows | `%APPDATA%\Hibi` |
| Linux | `~/.config/Hibi` |

API キーのファイルの既定の場所は `~/.config/minecontext/gemini-key` と `~/.config/minecontext/zhipu-key` です（環境変数 `GEMINI_KEY_FILE` / `ZHIPU_KEY_FILE` で変えられます）。

## 開発

```sh
git clone https://github.com/Kling0012/hibi.git
cd hibi
uv sync                                  # Python 3.12
uv run python -m opencontext.cli start   # バックエンド
cd frontend && pnpm install && pnpm run dev
```

必要なもの: Python 3.12 と [uv](https://docs.astral.sh/uv/)、Node.js 22 以上と pnpm 10。

### 配布用のビルド

Python 部分を PyInstaller で固めるため、**各 OS のビルドはその OS の上で**行います。GitHub Actions の `release.yml` を手動で動かすと、macOS / Windows / Linux 版を作って下書きのリリースに添付します。

```sh
gh workflow run release.yml -R Kling0012/hibi -f tag=v0.1.0
```

手元で作る場合は `frontend/` で `pnpm run build:mac` / `build:win` / `build:linux` です。

## 元のプロジェクトとの違い

[volcengine/MineContext](https://github.com/volcengine/MineContext) から、次を変えています。

- 名前とアイコンを Hibi に変更（以前の MineContext のデータは初回起動時に引き継ぎ）
- AI を複数の接続先から用途ごとに選び、失敗時に自動で切り替える方式に変更（Gemini Live、OpenAI 互換）
- 画面の記録を、Live API への連続送信と、変化時の正確な書き起こしの 2 段に変更
- 同期サーバへの送信、作業の宣言、同期サーバの画面の表示を追加
- フォルダ・文書・Web ページの取り込み、Doubao / Ark、補完機能、旧 Web 画面を削除
- 画面を日本語化し、サイドバーとホームを作り直し
- Electron 44、React 19、Python 3.12 / uv に更新
