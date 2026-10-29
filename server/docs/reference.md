# hibi-server 開発者向けリファレンス

複数端末（Mac/Windows）の Hibi 作業記録（イベント）を集約し、
時系列参照とハイブリッド検索（pgvector + pg_trgm）を提供する中央サーバ。
認証なし（tailnet 内限定運用を想定）。

## 構成

- Python 3.12 / FastAPI / SQLAlchemy 2 + psycopg 3 / Alembic
- PostgreSQL 17 + pgvector（`pgvector/pgvector:pg17` イメージ、拡張 `vector` `pg_trgm`）
- docker-compose.yml: `db` と `api` の2サービス。api は起動時に `alembic upgrade head` を自動実行。
- 依存は `pyproject.toml` + `uv.lock` で固定（Docker ビルドも lock ファイルから）。

## 起動方法（このリポジトリ）

```sh
cd sync-server
# 開発用（決定的な疑似埋め込み、外部通信なし）
sudo docker compose up -d --build
curl -s http://127.0.0.1:18741/healthz
```

環境変数（compose 経由。既定値は docker-compose.yml 参照）:

| 変数 | 既定 | 説明 |
|---|---|---|
| `API_PORT` | `18741` | ホスト側ポート |
| `BIND_HOST` | `127.0.0.1` | ホスト側バインド（tailnet で公開する場合は tailscale IP 等） |
| `EMBED_PROVIDER` | `fake` | `fake`（開発用・決定的・外部通信なし）、`openai_compat`、または `cloudflare`（Workers AI。agent-memory と同じ形式） |
| `EMBED_BASE_URL` | 空 | OpenAI 互換のベース URL（`.../v1` まで）。`openai_compat` 時必須 |
| `EMBED_MODEL` | `fake-embed` | 埋め込みモデル名（cloudflare は `@cf/qwen/qwen3-embedding-0.6b`） |
| `EMBED_DIM` | `1024` | 埋め込み次元。**マイグレーション時に vector 列の次元として固定される** |
| `EMBED_API_KEY_FILE` | 空 | API キーのファイルパス（秘密はファイル参照のみ。`openai_compat` 用） |
| `EMBED_CF_ENV_FILE` | 空 | `cloudflare` 用。`CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN` を含む env 形式ファイルのパス |
| `EMBED_CF_TIMEOUT_SEC` | `60` | `cloudflare` 通信のタイムアウト秒 |
| `EMBED_BATCH` | `64` | 1回の埋め込みリクエストに載せるイベント数の上限 |
| `EMBED_RELAY_MAX_INPUTS` | `64` | `POST /v1/embeddings`（中継）の1リクエストあたり入力数上限 |
| `EMBED_INTERVAL_SEC` | `5` | バックグラウンド埋め込みの周期 |
| `TZ_DEFAULT` | `Asia/Tokyo` | `GET /v1/days/{date}`・`GET /v1/timeline`・`GET /v1/stats/daily` の tz 既定値 |
| `LLM_PROVIDER` | `openai_compat` | 作業終了判定の LLM。`openai_compat`（`/chat/completions`）または `fake`（開発用・決定的・外部通信なし） |
| `LLM_BASE_URL` | 空 | OpenAI 互換のベース URL（`.../v1` まで）。`openai_compat` 時必須（未設定なら判定をスキップしてログのみ） |
| `LLM_MODEL` | `glm-5.3-flash` | 終了判定に使うモデル名 |
| `LLM_API_KEY_FILE` | 空 | API キーのファイルパス（秘密はファイル参照のみ） |
| `LLM_TIMEOUT_SEC` | `30` | LLM 通信のタイムアウト秒 |
| `SESSION_JUDGE_INTERVAL` | `120` | 終了判定の周期（秒） |
| `SESSION_END_THRESHOLD` | `0.75` | LLM 判定で終了扱いにする確信度のしきい値 |
| `IDLE_MINUTES` | `15` | 全端末でイベントが無い状態がこの分数続くと idle 終了 |
| `AM_IMPORT_ENABLED` | `1` | agent-memory からの取り込み有効/無効 |
| `AM_BASE_URL` | 空 | agent-memory のベース URL（例 `http://192.168.1.20:8377`）。空なら取り込みはアイドル |
| `AM_IMPORT_INTERVAL` | `300` | 取り込み周期（秒） |
| `AM_IMPORT_LOOKBACK_HOURS` | `48` | 初回（カーソル無し）の遡り時間（時） |
| `AM_TIMEOUT_SEC` | `15` | agent-memory 通信のタイムアウト秒 |
| `REPORT_LLM_MODEL` | `glm-5.3` | 日報生成に使うモデル（LLM_* の設定で通信） |
| `REPORT_LLM_FLASH_MODEL` | `glm-5.3-flash` | 材料が長い場合の時間帯ごとの事前要約に使うモデル |
| `REPORT_LLM_TIMEOUT_SEC` | `420` | 日報・ブログ生成の LLM 通信タイムアウト秒（長文生成向け） |
| `REPORT_CHUNK_CHARS` | `12000` | 材料の文字数がこれを超えたら時間帯（4時間区切り）ごとに flash モデルで要約してから統合 |
| `REPORT_AUTO_ENABLED` | `1` | 日報の自動生成（毎日 23:55 その日分・翌 06:00 前日分を force 再生成、TZ_DEFAULT）。失敗はログのみ |
| `YOMIYASU_POLISH` | `1` | 日報・ブログ本文の保存前の yomiyasu 推敲（内蔵 linter で点検し、候補があれば LLM で1回だけ書き直す。採用条件を満たさなければ元の文）。`0` で無効 |
| `BLOG_AUTO` | `draft` | ブログ自動執筆の挙動。`off`=スケジューラでの生成を停止、`draft`=自動生成記事は下書きに入るだけ（本人が確認して公開）、`publish`=即座に公開（旧動作） |
| `TODO_JUDGE_INTERVAL` | `300` | ToDo 作業時間の自動計上（照合ジョブ）の周期（秒） |
| `TODO_MATCH_THRESHOLD` | `0.7` | 計上に採用する LLM 判定の confidence しきい値（未満は破棄） |
| `TODO_JUDGE_MAX_WINDOW_MIN` | `30` | 照合に渡す作業時間の窓の上限（分）。初回は現在からこの分前以降に取り込んだ記録を対象にする |
| `TODO_MERGE_GAP_MIN` | `5` | 同じ ToDo の自動計上同士の隙間がこの分以内なら統合する。離席時間をまたぐ統合はしない |

注意: `EMBED_DIM` を変えると既存 DB の vector 列と不一致になるため、
新規ボリューム（`docker compose down -v`）で初期化すること。

## データモデル

- `devices(id PK, name, os, last_seen_at)`
- `events(id UUID PK=クライアント生成, device_id FK, kind, started_at, ended_at, app, title, text, meta jsonb, source, created_at, updated_at)`
  - kind は `activity|screen_summary|note|todo|ai_work|report` のみ（CHECK 制約）
  - インデックス: started_at / (device_id, started_at) / kind / GIN(text gin_trgm_ops) / GIN(meta)
- `event_embeddings(event_id PK FK CASCADE, model, embedding vector(EMBED_DIM), embedded_at)`
  - 埋め込みキューは持たず、「event_embeddings に無い events」を未処理とみなしてバックグラウンドで埋め込み。
    失敗しても events の保存・検索は止まらない（fail-soft。次の周期で再試行）。
- `work_sessions(id uuid PK, device_id（宣言した端末）, title, note, started_at, ended_at, status active/ended/cancelled, end_reason manual/ai_detected/idle/superseded, end_confidence, end_evidence jsonb（根拠にした event id と理由）, confirmed, created_at, updated_at)`
  - 「これから○○をする」の宣言。同時に active は1件だけで、新しい宣言は旧 active を
    `superseded` で終了させる（終了時刻 = 新しい宣言の開始時刻）。
  - 終了判定はバックグラウンド（`SESSION_JUDGE_INTERVAL` 秒ごと）。全端末の
    activity/screen_summary を最大30件集め、(1) `IDLE_MINUTES` 分どの端末からも
    イベントが無ければ `idle` 終了、(2) それ以外は LLM が JSON
    `{ongoing, ended_at_event_id, confidence, reason}` で判定し
    `ongoing=false` かつ `confidence >= SESSION_END_THRESHOLD` なら `ai_detected` 終了。
    終了時刻は「作業に関係した最後のイベント」の時刻。判定結果は `confirmed=false` のまま
    で、後から PATCH で修正・確定できる。LLM 失敗・設定なしはその回の判定スキップのみ
    （fail-soft。サーバ全体は止まらない）。`LLM_PROVIDER=fake` は title の単語が
    直近の記録から消えたら終了とみなす決定的ロジック（開始後3件未満は継続扱い）。
- `events.session_id uuid null`（FK, ON DELETE SET NULL）
  - `events:batch` で受けた activity/screen_summary は、その時刻に active な
    work_session があれば自動で紐づける。
- `importer_state(name PK, cursor_ts timestamptz, last_success_at, last_error, imported_count, updated_at)`
  - バックグラウンド取り込み（agent-memory → ai_work イベント）の進捗管理。
    cursor_ts は「最後に取り込んだ created_at（含む）」で、ページ単位で commit される
    （途中失敗しても続きから再試行）。

- `importer_state`（上記）: agent-memory 取り込みのカーソルと状態
- ToDo（P6、`todos` テーブル）: 新規追加は利用者が自分で足す ToDo（source='me'）
  のみ。AI エージェントが利用者の代わりに MCP で明示的に追加することもできる。
  既存の source='auto' の行は残す。
  `todos(id uuid, title(1〜200字), note, status open/done/archived, source me/auto,
  urgency high/medium/low, due_date, evidence「HH:MM 画面タイトル」形式の根拠,
  device_id, created_at, updated_at, done_at)`。
  `POST /v1/todos` の source 既定は 'me'。`source='auto'` は題名の重複に関係なく
  422（`"auto-created to-dos are disabled"`）を返す。source='me' は重複判定しない。
  `POST /v1/events:batch` の kind='todo' イベントは events に保存するが、
  ToDo は作らない。作業時間の自動計上は従来どおり続ける。
- `todo_time_entries(id uuid, todo_id FK CASCADE, started_at, ended_at,
  source auto/me, confidence, reason, event_ids jsonb, created_at)`:
  ToDo ごとの作業時間。auto は下の照合ジョブ、me は手動計上。
  REST で `GET /v1/todos/{id}/entries`（新しい順）・
  `DELETE /v1/todos/{id}/entries/{entry_id}`（誤計上の取り消し。物理削除）。
- ToDo 作業時間の自動計上（P6）: `TODO_JUDGE_INTERVAL` 秒ごとに、activity/
  screen_summary の変更を `(greatest(created_at, updated_at), id)` 順で最大500件取得する。
  取り込み時刻はコミット時刻ではなくトランザクション開始時刻なので、変更の取得と
  同じ SQL 文で `safe_before` を計算し、それより古い変更だけを取得する。
  `safe_before` は現在時刻と、同じ DB の他のクライアント接続で開いている
  トランザクションの最古の開始時刻の早い方とする。`backend_type='client backend'`
  に絞るため autovacuum などは待たないが、`idle in transaction` の接続は待つ。
  長時間開いたままのクライアントトランザクションがあると、それ以降の変更の
  自動計上は遅れる。遅れてコミットする変更を飛ばさないよう、待ち時間に上限は設けない。
  開始時刻による下限は設けず、古い作業の遅延到着や同じ ID の延長も再評価する。
  カーソルの時刻は `importer_state` の `todo_judge` 行、ID は同じテーブルの
  `todo_judge:cursor:<UUID>` 行の名前に保存する。最後に処理した組まで進めるため、
  同じ更新時刻の501件目も次の回で処理する。初回は現在から
  `TODO_JUDGE_MAX_WINDOW_MIN` 分前以降に取り込んだ記録を対象とする。
  ID のない旧カーソルからの切り替えでは、旧 `cursor_ts` と現在時刻の早い方から
  `TODO_JUDGE_MAX_WINDOW_MIN` 分さかのぼった時刻とゼロ UUID を開始点にする。
  変更時刻がこの開始点以降の記録を再評価し、全履歴の再生はしない。
  open の ToDo が0件でも変更を取得して最後の組までカーソルを進め、照合と計上だけを省く。
  照合と自動計上の置き換えは、作成日時・ID の昇順で選んだ open の ToDo の
  先頭30件に限る。この対象の ToDo では変更した記録に紐づく自動計上の
  `event_ids` に含まれる関連記録も読み直して置き換える。同じイベントの再送や再評価で
  時間を二重に加算しない。done・archived や31件目以降の open の ToDo に紐づく
  自動計上は置き換えず、その間も変更カーソルは進む。後から判定対象に入っても、
  処理済みの変更はそれだけでは再評価しない。手動計上は置き換えない。
  `ended_at` が判定時刻より未来なら、区間の終了を判定時刻で切り詰めるが、
  開始から最低30秒は確保する。長さ0や30秒未満の元の記録も、この段階で一度だけ
  30秒に延ばす。カーソルはその変更を処理済みにするため、イベントが更新されない限り、
  時刻の経過だけでは残りの区間を再評価しない。
  最低長を適用した作業時間は UTC 日付と `TODO_JUDGE_MAX_WINDOW_MIN` 分の窓で分け、
  対象の ToDo と記録（1回最大500件）を LLM へ渡す。
  `LLM_*` 設定を使い、`LLM_BASE_URL` が空ならジョブは何もしない。
  `[{"todo":番号,"start":"HH:MM:SS","end":"HH:MM:SS","confidence":0〜1,"reason":日本語60字}]`
  を返させる。「その ToDo を片付けるための作業をしていた時間だけ」を対象とし、
  関係のない閲覧や推測は含めないよう指示。`confidence ≥ TODO_MATCH_THRESHOLD`
  のものだけ計上し、判定区間と窓内の根拠区間が重なる部分から離席時間を除いて保存する。
  窓で分けた断片や離席控除後の断片には最低30秒を再適用せず、30秒未満でも残す。
  例えば09:29:50〜09:30:20 UTC の活動は10秒片と20秒片で合計30秒となり、
  UTC 日付境界をまたぐ場合も同じ扱いとする。該当記録の `event_ids` を付け、
  同じ ToDo の自動計上同士が重なるか、間が `TODO_MERGE_GAP_MIN` 分以内なら統合する。
  離席時間をまたぐ統合はしない。
  LLM 失敗時は計上とカーソルを変更せず、次の回で同じ変更をやり直す
  （fail-soft。止まるのは自動計上だけ）。`LLM_PROVIDER=fake` は title の
  2文字以上の語が記録の app/title/本文に含まれていれば confidence 0.9 で一致とし、
  区間の終了には `ended_at` を使う。
- 日報（P3-B）: `POST /v1/reports/daily` はその日の timeline（sessions・自分の
  activity/screen_summary・ai_work。superseded は非表示）を材料に LLM
  （`REPORT_LLM_MODEL`。材料が長い場合は 4 時間区切りで `REPORT_LLM_FLASH_MODEL`
  が事前要約）で日本語の Markdown 日報を生成し、`kind='report'` の event として
  保存する。id は日付から決まる uuid5（冪等。`force=true` で再生成）、
  device は `server`（自動登録）、`started_at` はその日の 00:00（tz）、
  meta に `{report_type:"daily", date, tz, model, source_counts, generated_at}`。
  日報は 6 節構成（概要 / 作業ごとの時間〔未確定に印〕 / 自分がしたこと /
  AI がしたこと〔type/tags 利用〕 / 自分とAIの作業の関係 / 明日に持ち越すこと）で、
  「材料に無いことは書かない」各記述に根拠時刻を括弧で添えるようプロンプトで指定。
  `LLM_PROVIDER=fake` は材料の件数だけを並べた決定的 Markdown（e2e 用）。
  バックグラウンドスケジューラ（fail-soft）が毎日 23:55（TZ_DEFAULT）にその日分を
  生成し、翌朝 06:00 に前日分を force で再生成する（深夜の作業を含めるため）。
- yomiyasu 推敲（日報・ブログ本文の保存前、共通）: 日本語の本文を保存する直前に
  内蔵の yomiyasu linter（`app/vendor/yomiyasu`。MIT、nanaism 製）で点検し、
  warn/error の候補があれば `REPORT_LLM_MODEL` に1回だけ書き直させる
  （依頼文は `app/vendor/yomiyasu/rules.md` の規則と候補一覧、
  「意味・事実・数字・時刻・固有名詞・Markdown の構造を変えない」）。
  書き直した文は、候補（warn+error）が減る・文字数が元の70〜130%・元にある
  `(HH:MM)` 形式の時刻がすべて残る、を満たすときだけ採用し、満たなければ元の文を
  使う。点検・書き直しの失敗（タイムアウト含む）はログ1行のみで、日報・ブログの
  生成自体は止まらない（fail-soft）。`LLM_PROVIDER=fake` は点検だけで書き直さない
  （e2e はこの経路）。`YOMIYASU_POLISH=0` で点検ごと無効になる。題名・タグには
  適用しない。
- ブログ（P5、`blog_posts` テーブル）: 日報の直後にその日の記事を自動生成する。
  2 種類: `summary`（1日1本。sessions・activity/screen_summary・ai_work を材料に
  一人称「私」・です・ます調の日記的な読み物。導入→何に取り組んだか→つまずき・
  判断・発見→学び→次にやること の流れ、見出し2〜4個、1500〜3000 字）と
  `learning`（type が solution/cause/experiment/result/issue の ai_work と
  screen_summary から再利用できる技術的知見を話題ごとに最大3本。「同じ問題に
  当たった人（未来の自分）」向けの技術ブログとして、導入で症状を具体的に示し
  原因と対処を順に語る。コマンド・設定は材料にあるものだけコードブロックで。
  最後の「## 参考」には根拠時刻と id〔memory id・コミットID等〕）。
  すべてのプロンプトで「材料に無い事実は書かない」（感想・意見は本人の発言と
  して材料にある範囲で）を明示し、保存前に伏せ字処理（Bearer 値・password= 等
  のキー値・32文字以上のキー形状トークン。uuid・git sha の hex トークンは参考
  掲載のため保持）を正規表現で適用。id=uuid5(date+type+topic)、
  slug=`<date>-summary` / `<date>-learning-<topic>`（冪等）。`force=true` で
  summary は同一 id で再生成する。learning は新しい全記事の本文などを先に検証し、
  管理対象の旧記事の削除と全記事の保存を一つのトランザクションで確定する。
  検証・保存が失敗した場合は旧記事を残し、途中まで保存した新記事も残さない。
  learning の置換対象は同日の `author=auto`・`status=draft` の記事のうち、
  生成用の id と slug が一致し、`model` が `api` ではなく、
  `updated_at=created_at` の未編集記事だけ。
  手動記事・外部著者の記事・公開済み記事・アーカイブ済み記事は保持する。
  `author=auto` の learning 記事を REST/MCP で編集すると、title・markdown・tags・excerpt の
  いずれかに値を指定した場合は `author=me` に変わり、以後の置換から外れる。
  同じ値の保存でも著者は変わる。status だけの変更では著者は変わらない。
  API が返す author は `me` になる。author を表示・絞り込みに使うクライアントでも、
  本人の記事として扱われる。現在の REST/MCP の記事一覧には author による絞り込みはない。
  著者の変更だけでは公開状態は変わらず、既定の公開一覧への掲載は status で決まる。
  `BLOG_AUTO=publish` は新たに生成・置換する記事に適用され、保護された編集済み learning 記事を
  自動公開・再生成することはない。summary・post の編集では、この著者変更は行わない。
  修正前のバージョンで編集された記事も、更新時刻が作成時刻と異なれば
  保持する。編集履歴のカラムがないため、状態変更などで更新された下書きも保持する。
  新しい topic が保持対象の slug と重なった場合は別の接尾辞を付けて保存する。
  保存時に id または slug が衝突した場合は全置換をロールバックし、上書きしない。
  `force=false` は従来どおり既存記事を返す。
  `LLM_PROVIDER=fake` は件数のみの決定的 Markdown（e2e 用）。
  日報スケジューラの各スロット（23:55 その日分・翌 06:00 前日分 force）の直後に
  同じ日のブログ2種類を生成（失敗はログのみ、fail-soft）。**自動生成記事の
  行き先は `BLOG_AUTO`**（既定 draft＝下書きに入るだけ。off なら生成しない。
  publish なら即公開）。
  記事の状態と著者（P5-B）: status は `draft` / `published` / `archived`、
  author は `"auto"`（内蔵 LLM）/ `"me"`（本人）/ `"agent:<名前>"`（外部 AI）。
  既定の公開 API（GET /v1/blog/posts 等）は **published のみ**返す
  （`?status=draft|published|archived|all` で切替）。DELETE は archived にする
  だけ（物理削除しない）。執筆用の REST（materials・作成・更新・公開・rewrite）
  と MCP（`/mcp`）は下の「AI にブログを書かせる」参照。
  閲覧画面（`app/blog/` の静的ファイル。Claude 担当）は `/blog/` として
  app/ui と同じ方式でマウント（ディレクトリが無ければマウントしない）。
- agent-memory からの取り込み（P3-A、バックグラウンド）:
  `GET {AM_BASE_URL}/v1/memories`（created_at 昇順・カーソルページング、クエリの
  ISO 時刻の「+」は %2B エンコード）を読み、各記録を events に upsert する。
  id=memory_id、kind='ai_work'、device_id='agent:<source.agent>'（devices に
  name=<agent>, os='agent' で自動登録）、started_at=created_at、text=content、
  title=type+先頭タグの短い見出し（例 "decision · MineContext"）、source=source.uri、
  meta={memory_type, tags, refs, source_session, valid_from, supersedes, superseded_by}。
  取り込み時刻帯に active な work_session があれば session_id を紐づける
  （events:batch と同じ規則。無ければ紐づけない）。supersede された記録は
  meta.superseded_by を付けた上で DB に残し、timeline では
  include_superseded=false（既定）で隠す。失敗（agent-memory 停止・タイムアウト）は
  ログと importer_state.last_error に記録するだけで次の周期で再試行する
  （fail-soft。止まるのは取り込みだけ）。初回は AM_IMPORT_LOOKBACK_HOURS から遡る。
  注意: api コンテナから agent-memory への経路はホストファイアウォールの影響を受ける
  （ufw の既定 DROP で、コンテナ→ホスト/テールネット IP が遮断されることがある。
  到達できない場合は ufw の許可か network_mode の調整が必要）。

## API 例（curl）

時刻は ISO8601 タイムゾーン付き。レスポンスの時刻は UTC (Z)。

```sh
B=http://127.0.0.1:18741

# ヘルスチェック
curl -s $B/healthz
# -> {"ok":true,"db":"ok","embed_pending":0}

# 端末登録（upsert。last_seen_at 更新）
curl -s -X PUT $B/v1/devices/mac-1 -H 'Content-Type: application/json' \
  -d '{"name":"MacBook Pro","os":"darwin"}'

# イベント一括投入（最大500。id 冪等 upsert: 同じ id は内容更新）
curl -s -X POST "$B/v1/events:batch" -H 'Content-Type: application/json' -d '{
  "device_id":"mac-1",
  "events":[{
    "id":"0192a0c0-0000-7000-8000-000000000001",
    "kind":"note",
    "started_at":"2026-10-12T09:00:00+09:00",
    "ended_at":"2026-10-12T09:30:00+09:00",
    "app":"Obsidian",
    "title":"週次メモ",
    "text":"四半期財務レポートのレビューをした",
    "meta":{"project":"minecontext"},
    "source":"obsidian-plugin"
  }]
}'
# -> {"accepted":1,"updated":0}   accepted=新規挿入, updated=同一id更新

# 一覧（started_at 昇順、カーソルページング）
curl -s "$B/v1/events?from=2026-10-12T00:00:00Z&device_id=mac-1&kind=note&limit=100"
curl -s "$B/v1/events?limit=100&cursor=<next_cursor>"

# ある日の全端末の時系列（端末名付き）と kind 別件数
curl -s "$B/v1/days/2026-10-12?tz=Asia/Tokyo" | jq '.counts, [.events[]|{t:.started_at,d:.device_name,txt:.text}]'

# ハイブリッド検索（pgvector cosine 上位50 と pg_trgm similarity 上位50 を RRF k=60 で統合）
# 埋め込み無しのイベントも trgm 側で拾う。各結果に score と根拠（vector_rank / trgm_rank）付き。
curl -s -X POST $B/v1/search -H 'Content-Type: application/json' -d '{
  "query":"財務レポート",
  "from":"2026-10-01T00:00:00Z", "to":"2026-11-01T00:00:00Z",
  "kinds":["note","report"], "device_ids":["mac-1"], "k":20
}'

# OpenAI 互換の埋め込み中継（PC クライアント向け）。中身は上と同じ EMBED_PROVIDER。
# model は無視されサーバ側の EMBED_MODEL を使う。input_type は互換性のためだけに
# 受け付け、結果は変わらない（前置きなし）。失敗は 502。認証なし（tailnet 内限定）。
curl -s -X POST $B/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-embedding-0.6b","input":["1件目","2件目"]}' | jq '.model, [.data[].index], (.data[0].embedding | length)'
# -> "fake-embed"（開発）/ "@cf/qwen/qwen3-embedding-0.6b"（本番）, [0,1], 1024

# 削除（単体のみ）
curl -s -X DELETE $B/v1/events/0192a0c0-0000-7000-8000-000000000001

# 作業宣言（新しい宣言は旧 active を superseded 終了させる）
curl -s -X POST $B/v1/sessions -H 'Content-Type: application/json' -d '{
  "title":"財務レポート", "note":"Q3のレビュー", "device_id":"mac-1"
}'
# -> {"session":{"id":"...","status":"active",...},"superseded":null}

# 現在の active な宣言
curl -s $B/v1/sessions/active

# 手動終了（manual・confirmed=true）
curl -s -X POST $B/v1/sessions/<id>/end -H 'Content-Type: application/json' \
  -d '{"ended_at":"2026-10-12T11:30:00+09:00"}'

# 修正・確定（title/note/started_at/ended_at/confirmed。AI 終了の確定に使う）
curl -s -X PATCH $B/v1/sessions/<id> -H 'Content-Type: application/json' \
  -d '{"title":"財務レポート（訂正）","ended_at":"2026-10-12T11:15:00+09:00","confirmed":true}'

# 取り消し（宣言を撤回）
curl -s -X POST $B/v1/sessions/<id>/cancel

# 一覧
curl -s "$B/v1/sessions?from=2026-10-12T00:00:00Z&status=ended"

# 1日分の統合タイムライン: sessions + events（端末名付き）+ ai_work を時刻順1本、
# セッション別の合計時間（cancelled を除く）付き
curl -s "$B/v1/timeline?date=2026-10-12&tz=Asia/Tokyo" | \
  jq '{total_minutes, totals: [.session_totals[]|{title,minutes,status}], items: [.items[]|{type,t,title:(.session.title // .event.title)}]}'
# supersede された ai_work（meta.superseded_by あり）は既定で隠れる。表示するには:
curl -s "$B/v1/timeline?date=2026-10-12&tz=Asia/Tokyo&include_superseded=true"

# 日別集計（ヒートマップ用）: 期間内の全日を返す（記録の無い日も 0 で）。
# events=全件数 / mine=kind != ai_work / ai_work / active_minutes・ai_active_minutes=
# started_at を5分刻みに丸めた異なる区間の数 x5 / session_minutes=その日に重なる
# work_sessions（cancelled 以外）を日境界で切って合計（active は now まで）。
# 期間は最大400日。日付の境界は tz で計算。1クエリで集計。
curl -s "$B/v1/stats/daily?from=2026-09-13&to=2026-10-12&tz=Asia/Tokyo" | jq '.days[-1]'

# agent-memory 取り込み（ai_work イベント）の状態と手動実行
curl -s $B/v1/importers
# -> {"importers":[{"name":"agent_memory","enabled":true,"cursor_ts":"...",
#     "last_success_at":"...","last_error":null,"imported_count":12,...}]}
curl -s -X POST $B/v1/importers/agent-memory/run
# -> {"ok":true,"fetched":3,"upserted":3,"pages":1,"cursor_ts":"..."}

# 日報の生成（材料 = その日の sessions + activity/screen_summary + ai_work）。
# id は日付からの uuid5 で冪等。既にあるなら generated=false で既存を返す。
curl -s -X POST $B/v1/reports/daily -H 'Content-Type: application/json' \
  -d '{"date":"2026-10-12","tz":"Asia/Tokyo","force":false}' | jq '.generated, .report.meta.source_counts'
# 取得・一覧
curl -s "$B/v1/reports/daily?date=2026-10-12" | jq -r .report.text
curl -s "$B/v1/reports?from=2026-10-01T00:00:00Z&to=2026-11-01T00:00:00Z" | jq '.events[].title'

# ブログ記事の生成（summary + learning。冪等。force で summary 同一 id 再生成・
# learning 同日入替。types で単独指定も可。生成された記事の status は BLOG_AUTO 次第）
curl -s -X POST $B/v1/blog/generate -H 'Content-Type: application/json' \
  -d '{"date":"2026-10-12","tz":"Asia/Tokyo","force":false}' \
  | jq '{summary: {slug: .summary.post.slug, generated: .summary.generated}, learning: [.learning[].post.slug]}'
# 一覧（新しい順。既定は published のみ。type/tag/q(title・markdown 部分一致)/
# status(draft|published|archived|all)/from/to フィルタ・cursor）
curl -s "$B/v1/blog/posts?type=&tag=&q=&status=all&limit=20" | jq '.posts[] | {slug,date,type,title,status,author}'
# 全文と前後の記事（?status= で下書き等も読める。前後記事は同じ status 範囲内）
curl -s "$B/v1/blog/posts/2026-10-12-summary" | jq '{title, prev, next}'
# タグ別件数（published のみ）
curl -s "$B/v1/blog/tags" | jq '.'
```

## AI にブログを書かせる（P5-B）

ブログを外部の AI（エージェント）や本人が直接書けるようにする API 群。
認証なし（他の API と同じく tailnet 内限定）。**運用ルール: 記事は必ず
下書き（draft）で作成し、本人が確認してから公開する。** 内蔵 LLM による
自動執筆も既定では下書きに入るだけ（`BLOG_AUTO=draft`）。

### 執筆材料を取得する

```sh
# その日の材料: 宣言された作業と時間 / 自分の記録 / AI の作業記録 / 日報本文。
# 長文は要約せず切り詰めるだけ（chars=自分の記録、ai_chars=ai_work の切り詰め長さ、
# max_records=各セクションの件数上限。切り詰めたら counts.*_truncated が true）。
curl -s "$B/v1/blog/materials?date=2026-10-12" | jq '.counts, .daily_report'
```

### REST で記事を書く

```sh
# 作成（slug は date＋英数字8字の短い id。伏せ字処理は自動。excerpt 無ければ自動生成）
curl -s -X POST $B/v1/blog/posts -H 'Content-Type: application/json' -d '{
  "title": "Hibi のブログ機能を作った",
  "markdown": "## 背景\n\n...",
  "date": "2026-10-13", "type": "post",
  "tags": ["hibi"], "author": "agent:claude", "status": "draft"
}' | jq '{slug, status, author}'

# 更新（指定項目のみ）・公開・非公開・アーカイブ
curl -s -X PATCH $B/v1/blog/posts/<slug> -H 'Content-Type: application/json' \
  -d '{"markdown": "## 背景\n\n（書き直し）"}' | jq '{slug, updated_at}'
curl -s -X POST $B/v1/blog/posts/<slug>/publish  | jq .status   # -> published
curl -s -X POST $B/v1/blog/posts/<slug>/unpublish | jq .status  # -> draft
curl -s -X DELETE $B/v1/blog/posts/<slug>         | jq .status  # -> archived（物理削除しない）

# 内蔵 LLM での書き直し案（保存しない。気に入ったら PATCH で保存）
curl -s -X POST $B/v1/blog/posts/<slug>/rewrite -H 'Content-Type: application/json' \
  -d '{"instruction": "導入をもう少し短く"}' | jq '.title, .markdown'
```

### MCP で書く（streamable HTTP、`/mcp`）

同じアプリが MCP サーバ（公式 Python SDK、stateless・JSON 応答）を
`/mcp` で提供する。読み取り系と書き込み系を説明で区別し、全ツールに
「下書きを作って本人の確認を経て公開する」運用が明記されている。

ツール一覧（読み取り）: `hibi_get_day_timeline(date)`,
`hibi_search_records(query, from_time?, to_time?, kinds?)`,
`hibi_get_blog_materials(date)`, `hibi_list_posts(status?, type?, limit?)`,
`hibi_get_post(slug)`, `hibi_list_todos(status?, limit?)`。
ツール一覧（書き込み）: `hibi_create_draft(title, markdown, date, type?,
tags?, author_name)`, `hibi_update_post(slug, ...)`,
`hibi_publish_post(slug)`, `hibi_unpublish_post(slug)`,
`hibi_add_todo(title, note?, urgency?, due_date?)`, `hibi_complete_todo(id)`。

Claude Code への登録例:

```sh
claude mcp add --transport http hibi http://<host>:18740/mcp
# （開発環境なら http://127.0.0.1:18741/mcp）
```

使う側の流れ: `hibi_get_blog_materials` と `hibi_get_day_timeline` で材料を集め →
`hibi_create_draft` で下書きを作り → 本人が確認したら `hibi_publish_post`。

## ToDo と作業時間の自動計上（P6）

ToDo の新規追加は利用者による追加（MCP での明示的な代理追加を含む）だけにする。
AI が画面の記録から見つけた ToDo は自動作成しない。既存の auto の行は残す。
画面の記録（activity / screen_summary）から「その ToDo に取り組んでいた時間」を
`todo_time_entries` に自動で足す処理は続ける（データモデルとジョブの詳細は上記）。

```sh
# 作成（source 既定は 'me'。urgency は high/medium/low、due_date は YYYY-MM-DD）
curl -s -X POST $B/v1/todos -H 'Content-Type: application/json' \
  -d '{"title":"quokka-pad の仕様を書く","urgency":"high","due_date":"2026-10-20"}' | jq .
# source='auto' は 422（"auto-created to-dos are disabled"）。source='me' は重複判定なし

# 一覧（?status=open|done|all。open は urgency 高→低・新しい順、done は done_at 新しい順）
# 各ToDo: tracked_minutes(合計分・小数1桁), last_tracked_at, tracking(直近10分以内に計上あれば true)
curl -s "$B/v1/todos?status=open" | jq '.todos[] | {title, urgency, tracked_minutes, tracking}'

# 更新（status='done' で done_at 設定・'open' に戻すと done_at クリア）
curl -s -X PATCH $B/v1/todos/<id> -H 'Content-Type: application/json' \
  -d '{"status":"done"}' | jq '{status, done_at}'
# 削除は archived にするだけ（物理削除しない）
curl -s -X DELETE $B/v1/todos/<id> | jq .status   # -> archived

# 作業時間の記録（新しい順。minutes は分）。誤計上は entry を物理削除して取り消す
curl -s "$B/v1/todos/<id>/entries" | jq '.entries[] | {started_at, minutes, confidence, reason}'
curl -s -X DELETE $B/v1/todos/<id>/entries/<entry_id> | jq .   # -> {"ok": true}

# タイムラインにその日の ToDo 計上が入る（既存の項目は不変）
curl -s "$B/v1/timeline?date=2026-10-13" | jq '.todo_entries'
```

MCP（`/mcp`）にも同じ操作が出る: 読み取り `hibi_list_todos(status?)`、
書き込み `hibi_add_todo(title, note?, urgency?, due_date?)`（source='me'）、
`hibi_complete_todo(id)`。

## E2E テスト

```sh
sudo ./scripts/e2e.sh
```

E2E 用の `scripts/compose.e2e.yml` を重ね、ネットワークに
`10.253.221.0/24` を指定する。Docker の既定アドレスプールを使い切った環境でも
起動できる。ローカルのネットワークと重なる場合は
`sudo env E2E_SUBNET=10.253.222.0/24 ./scripts/e2e.sh` のように未使用の範囲へ変更する。
初回の Compose 起動出力はエラーの原因を確認できるよう表示する。

docker compose up（fake 埋め込み + fake 終了判定、判定周期3秒・idle 12秒）→ healthz →
端末2台登録 → 2台分イベント投入（ja/en、同 id 再投入で更新）→ days で時系列・件数 →
カーソルページング → 検索（日本語部分一致とベクトル側一致）→ 削除 → 作業宣言と
activity/screen_summary の自動紐付け → 無関係なイベントが続くと ai_detected 終了
（終了時刻は最後の関係イベント時刻）→ 新しい宣言で旧 active が superseded →
イベント途絶えで idle 終了 → PATCH で修正と confirmed → cancel → timeline で
sessions・events・ai_work が時刻順に並びセッション別合計が出る → 埋め込みと LLM
到達不能時の投入・検索・宣言の継続 → 偽 agent-memory（scripts/fake_agent_memory.py を
compose の e2e プロファイルサービス `--profile e2e` として起動、2ページ・ページサイズ2、
同じネットワーク内なので本物には触らない）から ai_work 取り込み（タイムライン時刻順・superseded
既定で非表示・再実行で重複なし・active セッションへ紐付け）→ 偽サーバ停止中も
サーバは動き続け（run は 502・last_error 記録）復帰後にバックグラウンドで再取り込み、
→ 日別集計 stats/daily（記録の無い日が 0・tz 境界の日付・active_minutes の丸め・
セッションの日跨ぎ分割・期間超過 422）、→ 日報 reports/daily（fake: uuid5 の id・
meta.report_type/source_counts が timeline と一致・冪等再 POST・force 再生成・
GET/一覧・404）、→ LLM 到達不能時の生成は 502 でサーバは継続（日報・ブログとも
失敗時保存なし）、→ ブログ blog（fake: summary/learning の uuid5 id・slug・
材料件数・learning は type=solution の ai_work から1本・材料が無ければ 0 本・
冪等・force（summary 同一 id・learning 入替）・一覧（新しい順・type/tag/q
フィルタ・cursor）・全文と前後の記事・タグ別件数・404）、→ OpenAI 互換
`/v1/embeddings` 中継（単発・複数・入力順・次元=EMBED_DIM・決定性・64 件まで・
65 件で 422・空入力 422・不正 input_type 422・埋め込み到達不能時 502）、
→ ブログ執筆 API（materials の件数が timeline と一致・日報本文入り・
max_records で切り詰めフラグ・生成記事の BLOG_AUTO 既定 draft・REST での
下書き作成〔slug=日付+短い id・伏せ字・excerpt 自動生成〕・PATCH・publish/
unpublish で既定一覧の出し入れ・rewrite は提案のみで保存しない・DELETE は
archived のみ・?status= フィルタ）、→ MCP `/mcp`（initialize・tools/list 12
ツール・hibi_create_draft で下書き作成・hibi_publish_post で公開・未知ツールは
JSON-RPC エラー）、→ ToDo（作成・一覧〔open は urgency 順〕・source=auto は
題名の重複に関係なく 422〔source=me は重複判定なし〕・PATCH done/open で done_at の
出し入れ・DELETE は archived のみ・kind=todo イベント保存〔ToDo は作らない〕・
fake 照合で entry 作成〔confidence 0.9・event_ids 付き〕・5 分以内の継続で
直前の entry を延長・閾値未満は計上しない・entry 削除・timeline の
todo_entries・MCP の ToDo 3 ツール）、
を curl+jq で検証し PASS/FAIL を出す。
終了時に `compose down -v` で環境を消す。

`fake` provider はテキストの単語・文字2-gram を特徴ハッシュした決定的ベクトルで、
「意味検索」は n-gram 共有による近似（外部通信なし）。本番相当の意味検索には
`openai_compat` で実埋め込みモデルを指定すること。

e2e.sh の compose project 名は `hibi-e2e` に固定している（本番（COMPOSE_PROJECT_NAME、
既定 `hibi`）との分離）。

## `cloudflare` provider（Workers AI 埋め込み）

agent-memory（記憶システム）と同じ構成: OpenAI 互換エンドポイント
`POST https://api.cloudflare.com/client/v4/accounts/{id}/ai/v1/embeddings` に
`{"model","input"}` を送る（Bearer トークン認証）。クエリ・文書のどちらにも
instruction（前置き）は付けない — gateway は graphiti の OpenAI クライアントを
同じ `/ai/v1` に向けており、embedded に前置きを追加していない
（`agent-memory/gateway/app/graph_store.py` の `build_cloudflare_embedder_config`、
graphiti `graphiti_core/embedder/openai.py` の `create`/`create_batch`）。
アカウント ID とトークンは env 形式ファイル（`EMBED_CF_ENV_FILE`）から読み、
環境変数・コマンドラインには載せない。失敗時は既存の fail-soft
（events の保存・trgm 検索は止まらず、次周期で再試行）。

## OpenAI 互換埋め込み中継 `/v1/embeddings`（P1-C）

PC（Mac/Windows）の MineContext は埋め込みをこのサーバ経由で使う
（provider id `mcsync_embedding`、kind `openai_compat`、
`base_url: ${MINECONTEXT_SYNC_URL:http://<server-host>:18740}/v1`、
次元 1024）。PC に Cloudflare の秘密を配らずに済み、同期サーバと
記憶システム（agent-memory）で同じモデル・同じ空間になる。
リクエストは OpenAI 形式 `{model, input: str|list[str], input_type?}`
（`input_type` は `"query"`|`"document"`。互換性のためだけに受け付け、
結果は変わらない）、1 リクエスト最大 `EMBED_RELAY_MAX_INPUTS` 件。
レスポンスは `{object:"list", data:[{index, embedding}], model, usage}`。
`model` はサーバ側の `EMBED_MODEL` を返す（リクエストの model 名は無視）。
プロバイダ失敗時は 502（呼び出し側が fail-soft で扱う）。認証なし
（他の API と同じく tailnet 内限定）。usage のトークン数は文字数の近似。
