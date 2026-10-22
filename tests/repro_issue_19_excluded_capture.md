# Issue #19 再現記録

2026-10-22、修正前の `ai/issue-19`（`11c61b3`）で再現した。以下の修正前の結果とコード上の経路は、この時点の記録である。修正後と修正ラウンドの検証結果は末尾に記載した。

## 実行方法

リポジトリ直下で実行する。Node.js 22.13 以上、uv、Python 3.12 を使う。

```sh
uv run --no-project --python 3.12 --with pillow --with fastapi --with pyyaml --with loguru python tests/repro_issue_19_excluded_capture.py
```

実行環境は Linux、Node.js 22.23.4、Python 3.12.15、FastAPI 0.159.1。uv のキャッシュは worktree 外の `/home/wang/.cache/gh-auto/uv` を使った。frontend の依存インストールは不要。

Python スクリプトが単色の 2×2 JPEG を生成し、Node スクリプトへ渡す。Node は `LiveCaptureService.ts` を読み込み、Electron、get-windows、axios を代替して、実際の `getWindowMeta` と `tick` を実行する。取得許可、除外設定、generation を固定し、選択ディスプレイだけを返す。

Node が記録した POST の query と JPEG を、Python の実際の `post_live_frame` に Request として渡す。実際の `submit_frame` がキューに入れたフレームを、実際の `_handle_frame` で同期処理する。設定の参照、provider、画像ハッシュ、認証依存、レスポンスの整形はメモリ内の代替を使う。monitor のスレッド起動を抑え、provider の非同期メソッドが実行された回数を検証する。

実際の利用者データ、画面、パスワード、外部 AI 通信は使わない。`1Password` は合成ケースのアプリ名に限る。

## 修正前の結果

上のコマンドの終了コードは **1**。13 件中 4 件が成功し、9 件が期待値との不一致で失敗した。依存エラーや実行エラーは 0 件。Node の型除去と VM Modules に関する実験機能の警告は出る。

| 合成ケース | app | POST 回数 | accepted | provider 回数 | 検証結果 |
|---|---|---:|---|---:|---|
| 除外アプリが画面右端をまたぐ | 空文字 | 1 | true | 1 | 失敗 |
| 除外アプリが画面と同じ座標・サイズで最大化 | 1Password | 1 | false | 0 | 送信禁止の期待値で失敗 |
| 除外アプリの最大化枠が画面外へはみ出す | 空文字 | 1 | true | 1 | 失敗 |
| 負座標の画面で除外アプリが右端をまたぐ | 空文字 | 1 | true | 1 | 失敗 |
| メタデータ取得が例外になる・除外設定あり | 空文字 | 1 | true | 1 | 失敗 |
| メタデータ取得結果なし・除外設定あり | 空文字 | 1 | true | 1 | 失敗 |
| 通常アプリが画面右端をまたぐ | 空文字 | 1 | true | 1 | app 保持の期待値で失敗。処理継続と title/url 抑制は成功 |
| 通常アプリが画面内に収まる | Synthetic Editor | 1 | true | 1 | 成功 |
| メタデータ取得結果なし・除外設定なし | 空文字 | 1 | true | 1 | 成功 |

報告された条件は選択画面 `(0,0,1920,1080)`、最前面ウィンドウ `(1800,100,600,500)`。選択画面と 120px 分重なるが、取得したメタデータは `{"app":"","title":"","url":""}` となった。除外リストが `["1Password"]` でも、POST 1 回、`accepted=true`、代替 provider の `live_stream_frame` 実行 1 回を確認した。

バックエンドだけの対照・境界の確認も行った。

- `app="1Password"` をルートへ直接渡すと `excluded_app` で拒否され、provider は 0 回。成功。
- `app=""`、除外設定ありでは受理され、provider は 1 回。拒否を期待する検証が失敗。
- `app=""`、除外設定なしでは受理され、provider は 1 回。成功。
- 現在の generation と時刻を持つ、除外アプリの取り出し済みフレームを `_handle_frame` へ渡すと provider は 1 回。provider 呼び出し前の除外確認を期待する検証が失敗。このケースはキュー処理時の確認であり、通常の受理段階で既知の除外アプリが通過したという意味ではない。

初回は `pyyaml` がなく、設定パッケージの読み込みで 13 件ともエラーになった。依存指定と設定アクセサーの代替を補い、上記のコマンドで再実行した結果を再現の根拠にした。

## コード上の経路

- `frontend/src/main/services/LiveCaptureService.ts:187` の `getWindowMeta` は、完全包含でない場合に app/title/url が空の fallback を返す。
- 同ファイルの `tick`（125 行目以降）は空の app を使って JPEG を POST する。送信前に `exclude_apps` を照合していない。
- `opencontext/server/routes/live.py:38` のルートは query を `submit_frame` へ渡す。
- `opencontext/screen_record/live_monitor.py:365` の `_is_excluded` は空の app に `False` を返す。
- 同ファイルの `_handle_frame`（474 行目以降）と `_call_if_current`（294 行目以降）は、pause・generation・離席状態を確認するが、アプリ除外を確認せず Live provider を呼ぶ。

## 判断したこと

今回はバグ再現の工程なので、修正と FR の実装完了判定は次工程へ残した。再現スクリプトの作成だけが変更対象である。

除外設定ありで識別不能なら送信を止め、除外設定が空なら取得を続けるという既定の解釈を期待値にした。通常アプリの取得継続、包含されないウィンドウの title/url 抑制も同時に確認し、一律停止で対照ケースが失敗する構成にした。

最大化は画面と同じ座標・サイズのケースと、枠が各辺 8px はみ出すケースを合成した。後者が実 OS の返す座標だとは確認していない。

## 要件との対応

| 現在の FR | 再現スクリプトでの確認箇所 |
|---|---|
| FR-1、FR-9 | Node の `getWindowMeta` と Python の `normal_spanning` で app 保持を確認 |
| FR-2、FR-7 | Node の POST 記録と Python のルート・monitor・代替 provider の呼び出し回数 |
| FR-3、FR-10、FR-14 | `normal_spanning` の title/url が空であること |
| FR-4、FR-11 | メタデータの例外・欠落、バックエンドへの空 app、除外設定なしの対照 |
| FR-5 | `excluded_spanning` に報告と同じ座標を使用 |
| FR-6、FR-12 | 最大化、枠のはみ出し、負座標、メタデータの例外・欠落 |
| FR-8、FR-13 | `normal_contained`、`normal_spanning`、除外設定なしの対照 |

対象ファイルは `tests/repro_issue_19_excluded_capture.py` と `tests/repro_issue_19_excluded_capture.cjs`。この対応は再現検証の範囲であり、FR の修正完了を示すものではない。

## 未実行と未解決点

- 未実行: 実 OS・物理ディスプレイ・実画面・外部 provider の実通信。仕様で見送った確認であり、合成データと代替で検証した。
- 未実行: 実際の Electron アプリ起動、xvfb/CDP。報告と同じ合成条件を実際のサービスメソッドで実行した。
- 未実行: exact ルートの `generate` による書き起こし。今回確認した provider 境界は Live の `live_stream_frame`。
- 未実行: 全体テスト、アプリビルド、frontend の型検査。本体コードを変更しない再現工程のため、追加した再現スクリプトだけを実行した。
- 再現時点の未解決: 除外アプリの送信、空 app の受理、処理段階での除外確認不足。以下の修正後の検証で、これらの経路が拒否されることを確認した。

## 修正後の検証

2026-10-22、`edc4502` の本体コードで、実行方法に記載した既存の再現コマンドを実行した。終了コードは 0、13 件すべて成功した。テストコードは変更していない。

- 選択画面 `(0,0,1920,1080)` と除外ウィンドウ `(1800,100,600,500)` では app が `1Password` として残り、title/url は空になる。POST と provider 呼び出しはともに 0 回だった。
- 最大化、最大化枠のはみ出し、負座標、メタデータの例外・欠落でも、除外設定がある場合の POST と provider 呼び出しは 0 回だった。
- 通常アプリは画面内と画面またぎの両方で POST 1 回、provider 呼び出し 1 回だった。画面またぎでは title/url が空のままだった。除外設定なしの識別不能フレームも処理を継続した。
- バックエンドへ直接渡した除外アプリ、除外設定ありの空 app、取り出し済みの除外フレームからは provider が呼ばれなかった。

修正した本体 2 ファイルを一時的に `11c61b3` の内容へ戻し、同じコマンドで終了コード 1、13 件中 9 件の失敗を確認した。`finally` で復元すると終了コード 0、13 件成功となった。ログは worktree 外の `/home/wang/.cache/gh-auto/uv/hibi-19-revert-round-a9m68heh` に保存した。

## pnpm の起動失敗と復旧

修正ラウンドでは `/home/wang/.local/share/mise/installs/node/22/bin/pnpm -C frontend typecheck` を実行し、起動ファイル不在による終了コード 127 を再現した。`node/22` は `22.23.4` を指していた。Corepack は利用でき、`corepack pnpm --version` は `10.34.6` を返したが、同じ Node.js の `bin` に pnpm の起動ファイルがなかった。

現在の Node.js 環境で `corepack enable pnpm` を実行し、元の絶対パスから `pnpm --version` が `10.34.6` を返すことを確認した。README の開発手順にも、この復旧方法を追記した。検証環境の復旧であり、本体コードや依存関係の指定は変更していない。

復旧後に、失敗していた絶対パスのコマンドを再実行した。

- `pnpm -C frontend install --frozen-lockfile`: 終了コード 0。postinstall のネイティブ依存導入も完了した。
- `pnpm -C frontend typecheck`: 終了コード 0。node と web の型検査が成功した。
- `pnpm -C frontend lint`: 終了コード 0。エラー 0 件、警告 112 件。packageManager の一致も確認した。警告の修正は今回の対象に含めない。
- `uv sync` と `uv run python -c 'import opencontext'`: ともに終了コード 0。
- `tests/test_live_monitor_dedupe.py`: 終了コード 0、15 件成功。
- `repro_capture_pause.CapturePauseReproduction`: 終了コード 0、Python の一時停止検証 12 件成功。
- `node --test frontend/scripts/reproduce-live-capture.cjs`: 終了コード 0、12 件成功。

フルテスト・遅いテスト・配布ビルドは、作業中の検証を関連部分に絞る指定により未実行。実 OS・物理ディスプレイ・実画面・外部 AI 通信も仕様に従い未実行。exact の除外による `generate` の呼び出し抑止と、Electron 部分を含む `tests/repro_capture_pause.py` 全体は未実行であり、今回の合格範囲には含めない。

## 再審査ラウンドの再検証

2026-10-22、要件の完了報告が欠けていた指摘を受けて、`5bdf4ff` 時点のコードで検証をやり直した。本体コードとテストコードは変更していない。

- 実行方法に記載した再現コマンド: 終了コード 0、13 件成功。
- `pnpm -C frontend typecheck` と `pnpm -C frontend lint`: ともに終了コード 0。lint の警告 112 件は前回と同じ扱い。
- `uv sync`、`uv run python -c 'import opencontext'`、`uv run --with pytest pytest tests/test_live_monitor_dedupe.py`（15 件成功）、`node --test frontend/scripts/reproduce-live-capture.cjs`（12 件成功）、`PYTHONPATH=tests uv run --no-project --python 3.12 --with pyyaml --with loguru python -m unittest repro_capture_pause.CapturePauseReproduction`（12 件成功）: すべて終了コード 0。
- 戻し確認: 本体 2 ファイルを `11c61b3` の内容へ書き戻すと、再現コマンドは終了コード 1、13 件中 9 件失敗。`git checkout --` で元へ戻すと終了コード 0、13 件成功。ログは worktree 外の `/home/wang/.cache/gh-auto/uv/hibi-19-revert-round-b7k2fx9d` に保存した。

## 修正ラウンドの要件確認

2026-10-22、結果 JSON の requirements_done に FR-1〜FR-14 の完了が報告されていない指摘を受けて、`ce6650c` 時点のコードで各 FR の実装箇所を確認し、検証をやり直した。本体コードとテストコードは変更していない。

- FR-1・FR-3・FR-9・FR-10・FR-14: `frontend/src/main/services/LiveCaptureService.ts` の `getWindowMeta` は、選択画面と重なるウィンドウでは app（`identified`）を残し、完全包含でない場合は title と url を空にしたまま返す。重ならないウィンドウは従来どおり app も空の fallback を返す。
- FR-2・FR-4・FR-5・FR-7・FR-11: 同ファイルの `tick` は、除外設定があり app が空か除外リストのいずれかを含むとき、POST の前に次のソースへ進む。画面またぎの除外は `getWindowMeta` の重なり判定とこの確認で担保する。
- FR-8・FR-13: app が識別され除外に一致しないときは従来どおり POST する。除外設定がないときも識別できなくとも従来どおり処理する。
- FR-6・FR-12: 再現コマンドの最大化・枠のはみ出し・負座標・メタデータの例外と欠落の各ケースで、POST と provider 呼び出しがともに 0 回であることを確認した。
- バックエンド `opencontext/screen_record/live_monitor.py` の `_is_excluded` は、除外設定があるとき空の app を除外扱いにする。`_work_allowed` と `_call_if_current` が `_handle_frame`・`_process_exact`・`submit_frame` の各経路で除外を再確認し、キューに残ったフレームも provider 呼び出しに届かない。

検証コマンドは再審査ラウンドと同じものを実行し、再現コマンド（13 件成功）、`pnpm -C frontend typecheck`、`pnpm -C frontend lint`（警告 112 件は従来どおり）、`uv run python -c 'import opencontext'`、`uv run --with pytest pytest tests/test_live_monitor_dedupe.py`（15 件成功）、`node --test frontend/scripts/reproduce-live-capture.cjs`（12 件成功）、`repro_capture_pause.CapturePauseReproduction`（12 件成功）のいずれも終了コード 0 だった。

戻し確認もやり直した。本体 2 ファイルを `11c61b3` の内容へ戻すと再現コマンドは終了コード 1、13 件中 9 件失敗。`git checkout HEAD --` で戻すと終了コード 0、13 件成功になった。ログは worktree 外の `/home/wang/.cache/gh-auto/uv/hibi-19-revert-round-c-fix19` に保存した。
