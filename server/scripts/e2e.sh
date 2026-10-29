#!/usr/bin/env bash
# E2E test for hibi-server (docker).
# Usage: sudo ./scripts/e2e.sh
# Requires: docker compose, curl, jq, python3.
set -u
cd "$(dirname "$0")/.."

BASE="http://127.0.0.1:${API_PORT:-18741}"
# Fixed project name so dev/e2e stacks never collide with the production
# deployment (COMPOSE_PROJECT_NAME in .env, see docker-compose.prod.yml).
# Use an E2E-only subnet even when archived test networks exhaust Docker's
# default address pools. Keep the same files/subnet for every recreation.
DC=(env "E2E_SUBNET=${E2E_SUBNET:-10.253.221.0/24}" docker compose -p hibi-e2e
    -f docker-compose.yml -f scripts/compose.e2e.yml)
PASS=0
FAIL=0
DAY="$(date +%F)"
PDAY="$(date -d '-1 day' +%F)"   # §4-6 events: yesterday (always past)

ok()   { PASS=$((PASS+1)); echo "PASS: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $1"; }
check(){ if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (expected [$2], got [$1])"; fi; }

req() { # req METHOD PATH [JSON]
  local m=$1 p=$2 d=${3:-}
  if [ -n "$d" ]; then
    curl -s -X "$m" "$BASE$p" -H 'Content-Type: application/json' -d "$d"
  else
    curl -s -X "$m" "$BASE$p"
  fi
}

uuid() { python3 -c "import uuid;print(uuid.uuid4())"; }

cleanup() {
  echo "--- cleanup: compose down -v"
  sudo "${DC[@]}" --profile e2e down -v >/dev/null 2>&1
}
trap cleanup EXIT

echo "=== 1. compose up (EMBED_PROVIDER=fake, LLM_PROVIDER=fake, short judge/idle)"
sudo "${DC[@]}" --profile e2e down -v >/dev/null 2>&1
# NOTE: env must reach `docker compose` directly (a nested sudo would env-reset it).
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=fake SESSION_JUDGE_INTERVAL=3 IDLE_MINUTES=0.2 "${DC[@]}" up -d --build; then
  bad "docker compose up"
  echo "results: PASS=$PASS FAIL=$FAIL"; exit 1
fi
ok "docker compose up"

echo "=== 2. healthz"
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true and .db == "ok"' >/dev/null 2>&1 && break
  sleep 1
done
if echo "$HZ" | jq -e '.ok == true and .db == "ok"' >/dev/null 2>&1; then
  ok "healthz ok (embed_pending=$(echo "$HZ" | jq .embed_pending))"
else
  bad "healthz: $HZ"
fi

echo "=== 3. register 2 devices"
R=$(req PUT /v1/devices/mac-1 '{"name":"MacBook Pro","os":"darwin"}')
check "$(echo "$R" | jq -r .name)" "MacBook Pro" "device mac-1 upsert"
R=$(req PUT /v1/devices/win-1 '{"name":"Windowsデスクトップ","os":"windows"}')
check "$(echo "$R" | jq -r .os)" "windows" "device win-1 upsert"

echo "=== 4. ingest events from 2 devices (ja+en), idempotent re-ingest"
E1=$(uuid); E2=$(uuid); E3=$(uuid); E4=$(uuid)
mk() { # mk id device kind started text title
  printf '{"id":"%s","device_id":"%s","kind":"%s","started_at":"%s","text":%s,"title":%s}' \
    "$1" "$2" "$3" "$4" "$(jq -Rn --arg v "$5" '$v')" "$(jq -Rn --arg v "$6" '$v')"
}
B1="{\"device_id\":\"mac-1\",\"events\":[$(mk $E1 mac-1 note "${PDAY}T09:00:00+09:00" '四半期財務レポートのレビューをした' 'Q3 finances'),$(mk $E3 mac-1 activity "${PDAY}T11:00:00+09:00" 'VS Code で Python のコードを書いた' 'coding')]}"
B2="{\"device_id\":\"win-1\",\"events\":[$(mk $E2 win-1 screen_summary "${PDAY}T10:00:00+09:00" '会議の議事録: 予算について議論' 'meeting notes')]}"
R=$(req POST "/v1/events:batch" "$B1")
check "$(echo "$R" | jq -r '"\(.accepted)/\(.updated)"')" "2/0" "batch mac-1 accepted/updated"
R=$(req POST "/v1/events:batch" "$B2")
check "$(echo "$R" | jq -r '"\(.accepted)/\(.updated)"')" "1/0" "batch win-1 accepted/updated"
# same ids again with updated text -> updated, not duplicated
B1U="{\"device_id\":\"mac-1\",\"events\":[$(mk $E1 mac-1 note "${PDAY}T09:00:00+09:00" '四半期財務レポートのレビューを完了した' 'Q3 finances v2')]}"
R=$(req POST "/v1/events:batch" "$B1U")
check "$(echo "$R" | jq -r '"\(.accepted)/\(.updated)"')" "0/1" "re-ingest same id -> updated"
T=$(req GET "/v1/events?device_id=mac-1&limit=10" | jq -r '.events[] | select(.id=="'$E1'") | .text')
check "$T" "四半期財務レポートのレビューを完了した" "event content updated by re-ingest"

echo "=== 5. wait for fake embeddings then healthz pending=0"
for i in $(seq 1 20); do
  P=$(curl -s "$BASE/healthz" | jq .embed_pending)
  [ "$P" = "0" ] && break
  sleep 1
done
check "$P" "0" "embed_pending drains to 0 (fake provider)"

echo "=== 6. days: both devices chronological + kind counts"
D=$(req GET "/v1/days/${PDAY}?tz=Asia/Tokyo")
N=$(echo "$D" | jq '.events | length')
check "$N" "3" "days returns 3 events"
ORDER=$(echo "$D" | jq -r '[.events[].device_id] | join(",")')
check "$ORDER" "mac-1,win-1,mac-1" "days chronological across devices"
T0=$(echo "$D" | jq -r '.events[0].device_name')
check "$T0" "MacBook Pro" "days includes device name"
C=$(echo "$D" | jq -r '[.counts[].kind] | sort | join(",")')
check "$C" "activity,note,screen_summary" "days kind counts"

echo "=== 7. events cursor paging"
P1=$(req GET "/v1/events?limit=2")
N1=$(echo "$P1" | jq '.events | length'); CUR=$(echo "$P1" | jq -r .next_cursor)
P2=$(req GET "/v1/events?limit=2&cursor=$CUR")
N2=$(echo "$P2" | jq '.events | length')
NC2=$(echo "$P2" | jq -r .next_cursor)
if [ "$N1" = "2" ] && [ "$N2" = "1" ] && [ "$NC2" = "null" ]; then
  ok "cursor paging 2+1, last page has no cursor"
else
  bad "cursor paging (page1=$N1 page2=$N2 next=$NC2)"
fi

echo "=== 8. hybrid search: ja partial match + en semantic-ish match"
S=$(req POST /v1/search '{"query":"議事録","k":10}')
H=$(echo "$S" | jq -r '[.hits[].event.id] | index("'$E2'")')
[ "$H" != "null" ] && ok "ja partial match (trgm) finds meeting notes" || bad "ja partial match: $S"
S=$(req POST /v1/search '{"query":"quarterly financial report review","k":10,"kinds":["note"]}')
H=$(echo "$S" | jq -r '[.hits[].event.id] | index("'$E1'")')
[ "$H" != "null" ] && ok "en vector-side match finds Q3 note (kind filter ok)" || bad "en match: $S"
BASIS=$(req POST /v1/search '{"query":"予算","k":10}' | jq -r '.hits[0] | (.vector_rank!=null) or (.trgm_rank!=null)')
check "$BASIS" "true" "hit includes score basis (vector/trgm rank)"

echo "=== 9. delete single event"
R=$(req DELETE "/v1/events/$E3")
check "$(echo "$R" | jq 'has("deleted")')" "true" "delete returns deleted"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/v1/events/$E3")
check "$CODE" "404" "second delete -> 404"
N=$(req GET "/v1/events?device_id=mac-1&limit=10" | jq '[.events[].id] | index("'$E3'") != null')
check "$N" "false" "deleted event gone from list"

echo "=== 10. work sessions: declare -> related events auto-link"
S1=$(req POST /v1/sessions '{"title":"財務レポート","note":"Q3の数値確認","device_id":"mac-1"}')
SID1=$(echo "$S1" | jq -r .session.id)
check "$(echo "$S1" | jq -r .session.status)" "active" "declare session -> active"
check "$(echo "$S1" | jq -r .session.superseded)" "null" "first declare supersedes nothing"
R1=$(uuid); R2=$(uuid); R3=$(uuid)
RT1=$(date -u -d '+1 seconds' +%Y-%m-%dT%H:%M:%SZ)
RT2=$(date -u -d '+2 seconds' +%Y-%m-%dT%H:%M:%SZ)
RT3=$(date -u -d '+3 seconds' +%Y-%m-%dT%H:%M:%SZ)
RB="{\"device_id\":\"mac-1\",\"events\":[$(mk $R1 mac-1 activity "$RT1" '財務レポートの数値を確認している' 'finance'),$(mk $R2 mac-1 activity "$RT2" '財務レポートのグラフを作った' 'chart'),$(mk $R3 mac-1 screen_summary "$RT3" '画面:財務レポートのスプレッドシート' 'screen')]}"
R=$(req POST "/v1/events:batch" "$RB")
check "$(echo "$R" | jq -r .accepted)" "3" "related events ingested"
LNK=$(req GET "/v1/events?device_id=mac-1&limit=10" | jq -r ".events[] | select(.id==\"$R3\") | .session_id")
check "$LNK" "$SID1" "activity/screen_summary auto-linked to active session"
sleep 5
A=$(req GET /v1/sessions/active | jq -r .session.id)
check "$A" "$SID1" "session stays active while related events continue"

echo "=== 11. unrelated events -> ai_detected end at last related event"
U1=$(uuid); U2=$(uuid); U3=$(uuid)
UT1=$(date -u +%Y-%m-%dT%H:%M:%SZ)
UT2=$(date -u -d '+1 seconds' +%Y-%m-%dT%H:%M:%SZ)
UT3=$(date -u -d '+2 seconds' +%Y-%m-%dT%H:%M:%SZ)
UB="{\"device_id\":\"mac-1\",\"events\":[$(mk $U1 mac-1 activity "$UT1" '家族の旅行の写真を見ている' 'photos'),$(mk $U2 mac-1 activity "$UT2" '音楽を聴きながらWebを見る' 'browse'),$(mk $U3 mac-1 screen_summary "$UT3" '画面:動画サイト' 'video')]}"
R=$(req POST "/v1/events:batch" "$UB")
check "$(echo "$R" | jq -r .accepted)" "3" "unrelated events ingested"
ST=""
for i in $(seq 1 20); do
  ST=$(req GET "/v1/sessions/$SID1" | jq -r .status)
  [ "$ST" = "ended" ] && break
  sleep 1
done
check "$ST" "ended" "session ended after unrelated events (ai_detected)"
S=$(req GET "/v1/sessions/$SID1")
check "$(echo "$S" | jq -r .end_reason)" "ai_detected" "end_reason=ai_detected"
check "$(echo "$S" | jq -r .confirmed)" "false" "ai end leaves confirmed=false"
check "$(echo "$S" | jq -r '.end_evidence[0].event_id')" "$R3" "evidence points at last related event"
ET=$(req GET "/v1/events?device_id=mac-1&limit=50" | jq -r ".events[] | select(.id==\"$R3\") | .started_at")
check "$(echo "$S" | jq -r .ended_at)" "$ET" "ended_at = last related event time (not judge time)"

echo "=== 12. supersede / idle end / PATCH / cancel"
S2=$(req POST /v1/sessions '{"title":"メール処理","device_id":"win-1"}')
SID2=$(echo "$S2" | jq -r .session.id)
S3=$(req POST /v1/sessions '{"title":"資料読み","device_id":"mac-1"}')
SID3=$(echo "$S3" | jq -r .session.id)
check "$(echo "$S3" | jq -r .superseded.id)" "$SID2" "new declare returns superseded session"
S2R=$(req GET "/v1/sessions/$SID2")
check "$(echo "$S2R" | jq -r .status)" "ended" "old active session superseded -> ended"
check "$(echo "$S2R" | jq -r .end_reason)" "superseded" "end_reason=superseded"
check "$(echo "$S2R" | jq -r .ended_at)" "$(echo "$S3" | jq -r .session.started_at)" "superseded ended_at = new started_at"
A=$(req GET /v1/sessions/active | jq -r .session.id)
check "$A" "$SID3" "newest declaration is the active session"
RL=$(uuid); RLT=$(date -u -d '+5 seconds' +%Y-%m-%dT%H:%M:%SZ)
# +5s: keeps RL strictly later than section 11's U3 (+3s there), so the
# judge's "last event" (idle evidence / ended_at) is always RL regardless
# of how fast the intermediate poll loops finish.
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(mk $RL mac-1 activity "$RLT" '資料読み:設計書の章を読み進めた' 'reading')]}")
LNK=$(req GET "/v1/events?device_id=mac-1&kind=activity&limit=50" | jq -r ".events[] | select(.id==\"$RL\") | .session_id")
check "$LNK" "$SID3" "event links to the new active session"
ST=""
for i in $(seq 1 40); do
  ST=$(req GET "/v1/sessions/$SID3" | jq -r .status)
  [ "$ST" = "ended" ] && break
  sleep 1
done
check "$ST" "ended" "session idle-ended after events stop"
S=$(req GET "/v1/sessions/$SID3")
check "$(echo "$S" | jq -r .end_reason)" "idle" "end_reason=idle"
check "$(echo "$S" | jq -r '.end_evidence[0].event_id')" "$RL" "idle evidence = last event"
RLET=$(req GET "/v1/events?device_id=mac-1&kind=activity&limit=50" | jq -r ".events[] | select(.id==\"$RL\") | .started_at")
check "$(echo "$S" | jq -r .ended_at)" "$RLET" "idle ended_at = last event time"
NEWEND=$(date -u -d "@$(( $(date -u -d "$RLT" +%s) + 300 ))" +%Y-%m-%dT%H:%M:%SZ)
P=$(req PATCH "/v1/sessions/$SID3" "{\"title\":\"資料読み(修正済み)\",\"ended_at\":\"$NEWEND\",\"confirmed\":true}")
check "$(echo "$P" | jq -r .title)" "資料読み(修正済み)" "PATCH updates title"
check "$(echo "$P" | jq -r .confirmed)" "true" "PATCH confirms session"
check "$(date -u -d "$(echo "$P" | jq -r .ended_at)" +%s)" "$(date -u -d "$NEWEND" +%s)" "PATCH sets ended_at"
S4=$(req POST /v1/sessions '{"title":"中止する宣言"}')
SID4=$(echo "$S4" | jq -r .session.id)
C=$(req POST "/v1/sessions/$SID4/cancel")
check "$(echo "$C" | jq -r .status)" "cancelled" "cancel -> cancelled"
A=$(req GET /v1/sessions/active | jq -r .session)
check "$A" "null" "no active session remains"

echo "=== 13. timeline: sessions + events + ai_work chronological, per-session totals"
AW=$(uuid); AWT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"win-1\",\"events\":[{\"id\":\"$AW\",\"kind\":\"ai_work\",\"started_at\":\"$AWT\",\"text\":\"Claude が P2B セッション機能を実装した\",\"title\":\"P2B 実装\",\"source\":\"claude\",\"meta\":{\"agent\":\"claude\"}}]}")
check "$(echo "$R" | jq -r .accepted)" "1" "ai_work event ingested"
TL=$(req GET "/v1/timeline?date=${DAY}&tz=Asia/Tokyo")
# compare at second precision: t may mix micro (sessions) and second (events) forms
check "$(echo "$TL" | jq -r '[.items[].t | sub("\\.[0-9]+";"")] == ([.items[].t | sub("\\.[0-9]+";"")]|sort)')" "true" "timeline items are chronological"
check "$(echo "$TL" | jq -r '[.items[] | select(.type=="session") | .session.id] | index("'$SID1'") != null')" "true" "timeline includes sessions"
check "$(echo "$TL" | jq -r '.items[] | select(.type=="ai_work") | .event.source')" "claude" "ai_work item carries source"
check "$(echo "$TL" | jq -r '.items[] | select(.type=="ai_work") | .event.device_name')" "Windowsデスクトップ" "timeline events carry device name"
check "$(echo "$TL" | jq -r '.session_totals[] | select(.session_id=="'$SID3'") | .minutes > 4')" "true" "per-session total minutes (s3 ~5min)"
check "$(echo "$TL" | jq -r '.total_minutes > 0')" "true" "total_minutes present"

echo "=== 14. embedder + LLM unreachable: ingest, sessions and search keep working"
sudo env EMBED_PROVIDER=openai_compat EMBED_BASE_URL=http://10.255.255.1:1/v1 EMBED_MODEL=test \
  LLM_PROVIDER=openai_compat LLM_BASE_URL=http://10.255.255.1:1/v1 LLM_TIMEOUT_SEC=5 SESSION_JUDGE_INTERVAL=5 \
  "${DC[@]}" up -d api >/dev/null 2>&1 || bad "recreate api with unreachable providers"
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && break
  sleep 1
done
echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && ok "healthz ok with failing embedder" || bad "healthz with failing embedder: $HZ"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/embeddings" \
  -H 'Content-Type: application/json' -d '{"model":"m","input":"relay test"}')
check "$CODE" "502" "embeddings relay reports 502 while embedder unreachable"
E5=$(uuid)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"win-1\",\"events\":[$(mk $E5 win-1 note "${DAY}T12:00:00+09:00" '障害時でも登録できるメモ' 'resilient note')]}")
check "$(echo "$R" | jq -r .accepted)" "1" "ingest works while embedder fails"
S=$(req POST /v1/search '{"query":"障害時でも登録","k":10}')
H=$(echo "$S" | jq -r '[.hits[].event.id] | index("'$E5'")')
[ "$H" != "null" ] && ok "search (trgm) works while embedder fails" || bad "search during embed failure: $S"
P=$(curl -s "$BASE/healthz" | jq .embed_pending)
if [ "$P" -ge 1 ] 2>/dev/null; then ok "embed_pending grows ($P) but service stays up"; else bad "embed_pending=$P"; fi
S5=$(req POST /v1/sessions '{"title":"障害時の作業"}')
SID5=$(echo "$S5" | jq -r .session.id)
check "$(echo "$S5" | jq -r .session.status)" "active" "declare works while LLM unreachable"
# an event is needed so the judge reaches the LLM call (no-event sessions idle
# out before the LLM is consulted); +1s keeps it after the session's start
E6=$(uuid); E6T=$(date -u -d '+1 seconds' +%Y-%m-%dT%H:%M:%SZ)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"win-1\",\"events\":[{\"id\":\"$E6\",\"kind\":\"activity\",\"started_at\":\"$E6T\",\"text\":\"障害時でも活動を記録する\",\"title\":\"resilient activity\"}]}")
check "$(echo "$R" | jq -r .accepted)" "1" "ingest works while LLM unreachable"
sleep 8
check "$(curl -s "$BASE/healthz" | jq -r .ok)" "true" "healthz still ok with failing judge"
A=$(req GET /v1/sessions/active | jq -r .session.id)
check "$A" "$SID5" "session stays active when judge fails (fail-soft)"
JLOG=""
for i in $(seq 1 25); do
  if sudo "${DC[@]}" logs api 2>&1 | grep -q "session judge failed"; then JLOG=1; break; fi
  sleep 1
done
if [ -n "$JLOG" ]; then
  ok "judge failure logged (skips cycle only, server keeps running)"
else
  bad "no judge failure log found"
fi

echo "=== 15. agent-memory import: ai_work timeline, idempotent, superseded, fail-soft"
AM1=00000000-0000-4000-8000-00000000a001
AM2=00000000-0000-4000-8000-00000000a002
AM3=00000000-0000-4000-8000-00000000a003
AM4=00000000-0000-4000-8000-00000000a004
AM5=00000000-0000-4000-8000-00000000a005
# fake agent-memory runs as an e2e-profile compose service (same network as
# the api, so no host firewall is involved); page-size 2 forces cursor paging
sudo "${DC[@]}" --profile e2e up -d fake-agent-memory >/dev/null 2>&1 || bad "start fake agent-memory"
# judge interval 600s: no judging races during this section
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=fake SESSION_JUDGE_INTERVAL=600 IDLE_MINUTES=15 \
     AM_BASE_URL=http://fake-agent-memory:8377 AM_IMPORT_INTERVAL=3 \
     "${DC[@]}" up -d api >/dev/null 2>&1; then
  bad "recreate api with agent-memory import"
fi
N=0; ERR="x"
for i in $(seq 1 40); do
  IMP=$(curl -s "$BASE/v1/importers" || true)
  N=$(echo "$IMP" | jq -r '.importers[] | select(.name=="agent_memory") | (.imported_count // 0)')
  ERR=$(echo "$IMP" | jq -r '.importers[] | select(.name=="agent_memory") | (.last_error // "")')
  [ "${N:-0}" -ge 4 ] 2>/dev/null && [ -z "$ERR" ] && break
  sleep 1
done
if [ "${N:-0}" -ge 4 ] 2>/dev/null; then
  ok "importer pulled all 4 records (page-size 2 -> cursor paging worked)"
else
  bad "importer did not finish (count=${N:-none} err=$ERR)"
fi
check "$(echo "$IMP" | jq -r '.importers[] | select(.name=="agent_memory") | .enabled')" "true" "importer reports enabled"
check "$(echo "$IMP" | jq -r '.importers[] | select(.name=="agent_memory") | .cursor_ts != null')" "true" "importer cursor advanced"
EVC=$(req GET "/v1/events?kind=ai_work&limit=100" | jq '[.events[] | select(.device_id | startswith("agent:"))] | length')
check "$EVC" "4" "4 ai_work events stored from agent devices"
am_event() { req GET "/v1/events?kind=ai_work&limit=100" | jq -c ".events[] | select(.id==\"$1\")"; }
M1=$(am_event $AM1)
check "$(echo "$M1" | jq -r .device_id)" "agent:claude" "memory -> device agent:claude"
check "$(echo "$M1" | jq -r .title)" "decision · minecontext" "title from type + first tag"
check "$(echo "$M1" | jq -r .source)" "e2e:/fake/project" "source = source.uri"
check "$(echo "$M1" | jq -r .meta.memory_type)" "decision" "meta.memory_type kept"
check "$(echo "$M1" | jq -r .meta.superseded_by)" "$AM4" "superseded record marked via supersedes link"
check "$(am_event $AM2 | jq -r .device_id)" "agent:codex" "second agent -> device agent:codex"
TL=$(req GET "/v1/timeline?date=${DAY}&tz=Asia/Tokyo")
AWD=$(echo "$TL" | jq -r '[.items[] | select(.type=="ai_work") | select(.event.device_id | startswith("agent:"))] | length')
check "$AWD" "3" "timeline hides superseded ai_work by default"
ORD=$(echo "$TL" | jq -r '[.items[] | select(.type=="ai_work") | select(.event.device_id | startswith("agent:")) | .event.id] | join(",")')
check "$ORD" "$AM2,$AM3,$AM4" "ai_work items chronological in timeline"
DN=$(echo "$TL" | jq -r ".items[] | select(.type==\"ai_work\") | select(.event.id==\"$AM2\") | .event.device_name")
check "$DN" "codex" "timeline ai_work carries agent device name"
AWS=$(req GET "/v1/timeline?date=${DAY}&tz=Asia/Tokyo&include_superseded=true" | jq -r '[.items[] | select(.type=="ai_work") | select(.event.device_id | startswith("agent:"))] | length')
check "$AWS" "4" "include_superseded=true shows all 4"
RUN=$(curl -s -X POST "$BASE/v1/importers/agent-memory/run")
check "$(echo "$RUN" | jq -r .ok)" "true" "manual import run ok"
EVC2=$(req GET "/v1/events?kind=ai_work&limit=100" | jq '[.events[] | select(.device_id | startswith("agent:"))] | length')
check "$EVC2" "4" "re-run does not duplicate (id = memory_id upsert)"
S6=$(req POST /v1/sessions "{\"title\":\"AIの作業記録\",\"started_at\":\"$(date -u -d '-2 hours' +%Y-%m-%dT%H:%M:%SZ)\"}")
SID6=$(echo "$S6" | jq -r .session.id)
curl -s -X POST "$BASE/v1/importers/agent-memory/run" >/dev/null
LNK=$(am_event $AM4 | jq -r .session_id)
check "$LNK" "$SID6" "ai_work links to session active at its time"
sudo "${DC[@]}" --profile e2e stop fake-agent-memory >/dev/null 2>&1
sleep 1
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/importers/agent-memory/run")
check "$CODE" "502" "manual run reports 502 while agent-memory down"
check "$(curl -s "$BASE/healthz" | jq -r .ok)" "true" "server stays up while agent-memory down"
ERR=""
for i in $(seq 1 10); do
  ERR=$(req GET /v1/importers | jq -r '.importers[] | select(.name=="agent_memory") | (.last_error // "")')
  [ -n "$ERR" ] && break
  sleep 1
done
[ -n "$ERR" ] && ok "last_error recorded while down" || bad "no last_error recorded"
if sudo env FAKE_AM_ARGS=--extra "${DC[@]}" --profile e2e up -d fake-agent-memory >/dev/null 2>&1; then
  ok "fake agent-memory restarted with an extra record"
else
  bad "fake agent-memory restart failed"
fi
EVC3=""
for i in $(seq 1 40); do
  EVC3=$(req GET "/v1/events?kind=ai_work&limit=100" | jq '[.events[] | select(.device_id | startswith("agent:"))] | length')
  ERR=$(req GET /v1/importers | jq -r '.importers[] | select(.name=="agent_memory") | (.last_error // "")')
  [ "$EVC3" = "5" ] && [ -z "$ERR" ] && break
  sleep 1
done
check "$EVC3" "5" "background import recovers after agent-memory returns"
check "$(req GET /v1/importers | jq -r '.importers[] | select(.name=="agent_memory") | .last_success_at != null')" "true" "last_success_at set after recovery"
AWF=$(req GET "/v1/timeline?date=${DAY}&tz=Asia/Tokyo" | jq -r '[.items[] | select(.type=="ai_work") | select(.event.device_id | startswith("agent:"))] | length')
check "$AWF" "4" "timeline shows recovered record (superseded still hidden)"

echo "=== 16. stats/daily: empty days, tz boundary, 5-min rounding, session day-split"
D1="$(date -d '+1 day' +%F)"; D2="$(date -d '+2 day' +%F)"; D3="$(date -d '+3 day' +%F)"
# DAY 19:00Z == DAY+1 04:00+09:00 -> belongs to DAY+1 in Asia/Tokyo, DAY in UTC
EA=$(uuid)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"win-1\",\"events\":[$(mk $EA win-1 note "${DAY}T19:00:00Z" 'tz境界のメモ' 'tz edge')]}")
check "$(echo "$R" | jq -r .accepted)" "1" "boundary event ingested"
# clean day D2: 09:01/09:04 same 5-min bucket, 09:07 next bucket; ai_work both in one bucket
B1=$(uuid); B2=$(uuid); B3=$(uuid); AI1=$(uuid); AI2=$(uuid)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(mk $B1 mac-1 note "${D2}T09:01:00+09:00" '丸め1' 'r1'),$(mk $B2 mac-1 note "${D2}T09:04:00+09:00" '丸め2' 'r2'),$(mk $B3 mac-1 note "${D2}T09:07:00+09:00" '丸め3' 'r3'),$(mk $AI1 mac-1 ai_work "${D2}T10:01:00+09:00" 'AI作業1' 'ai1'),$(mk $AI2 mac-1 ai_work "${D2}T10:03:00+09:00" 'AI作業2' 'ai2')]}")
check "$(echo "$R" | jq -r .accepted)" "5" "bucket-rounding events ingested"
# session crossing midnight JST: DAY 23:00 -> DAY+1 01:00 (60min on each day)
SS=$(req POST /v1/sessions "{\"title\":\"日跨ぎ作業\",\"device_id\":\"mac-1\",\"started_at\":\"${DAY}T23:00:00+09:00\"}")
SSID=$(echo "$SS" | jq -r .session.id)
SE=$(req POST "/v1/sessions/$SSID/end" "{\"ended_at\":\"${D1}T01:00:00+09:00\"}")
check "$(echo "$SE" | jq -r .status)" "ended" "cross-midnight session ended"

ST=$(req GET "/v1/stats/daily?from=${DAY}&to=${D3}")
check "$(echo "$ST" | jq -r .tz)" "Asia/Tokyo" "default tz is Asia/Tokyo"
check "$(echo "$ST" | jq '.days | length')" "4" "all days in range returned (incl. empty)"
J1=$(echo "$ST" | jq -c ".days[] | select(.date==\"$D1\")")
check "$(echo "$J1" | jq -r '[.events,.mine,.ai_work,.active_minutes,.ai_active_minutes] | @tsv')" "1	1	0	5	0" "D1(JST): boundary event counts (mine bucket)"
check "$(echo "$J1" | jq '.session_minutes == 60')" "true" "D1(JST): 60min session part after midnight"
J2=$(echo "$ST" | jq -c ".days[] | select(.date==\"$D2\")")
check "$(echo "$J2" | jq -r '[.events,.mine,.ai_work,.active_minutes,.ai_active_minutes] | @tsv')" "5	3	2	10	5" "D2(JST): 5-min bucket rounding (2 mine + 1 ai buckets)"
check "$(echo "$J2" | jq '.session_minutes == 0')" "true" "D2(JST): no session overlap"
check "$(echo "$J2" | jq -r .first_at)" "${D2}T00:01:00Z" "D2 first_at (09:01 JST in UTC)"
check "$(echo "$J2" | jq -r .last_at)" "${D2}T01:03:00Z" "D2 last_at (10:03 JST in UTC)"
J3=$(echo "$ST" | jq -c ".days[] | select(.date==\"$D3\")")
check "$(echo "$J3" | jq -r '[.events,.mine,.ai_work,.active_minutes,.ai_active_minutes,(.session_minutes==0),(.first_at==null),(.last_at==null)] | @tsv')" "0	0	0	0	0	true	true	true" "day without records is all zeros/nulls"
JD=$(echo "$ST" | jq -c ".days[] | select(.date==\"$DAY\")")
check "$(echo "$JD" | jq '.session_minutes >= 60')" "true" "cross-midnight session gives DAY its 60min part"
STU=$(req GET "/v1/stats/daily?from=${DAY}&to=${D1}&tz=UTC")
check "$(echo "$STU" | jq -r ".days[] | select(.date==\"$D1\") | [.events,.mine] | @tsv")" "0	0" "tz=UTC: boundary event not on DAY+1"
check "$(echo "$STU" | jq '.days[] | select(.date=="'$DAY'") | .mine >= 2')" "true" "tz=UTC: boundary event counted on DAY instead"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/v1/stats/daily?from=${DAY}&to=$(date -d '+401 day' +%F)")
check "$CODE" "422" "range over 400 days -> 422"

echo "=== 17. daily report (fake): uuid5 id, idempotent, force, get, list"
RP=$(req POST /v1/reports/daily "{\"date\":\"${DAY}\"}")
check "$(echo "$RP" | jq -r .generated)" "true" "daily report generated (fake provider)"
RID=$(python3 -c "import uuid;print(uuid.uuid5(uuid.NAMESPACE_URL,'minecontext:sync-server:daily-report:${DAY}'))")
check "$(echo "$RP" | jq -r .report.id)" "$RID" "report id is uuid5 of the date"
check "$(echo "$RP" | jq -r .report.kind)" "report" "stored as kind=report"
check "$(echo "$RP" | jq -r .report.title)" "日報 ${DAY}" "report title"
check "$(echo "$RP" | jq -r .report.meta.report_type)" "daily" "meta.report_type=daily"
check "$(echo "$RP" | jq -r .report.meta.date)" "$DAY" "meta.date"
check "$(echo "$RP" | jq -r .report.meta.model)" "fake" "meta.model=fake"
check "$(echo "$RP" | jq -r .report.device_name)" "sync-server" "report stored on the server device"
# source_counts must match the day's timeline material
TL=$(req GET "/v1/timeline?date=${DAY}&tz=Asia/Tokyo")
SES=$(echo "$TL" | jq '.session_totals | length')
ACT=$(echo "$TL" | jq '[.items[] | select(.type=="event" and .event.kind=="activity")] | length')
SCR=$(echo "$TL" | jq '[.items[] | select(.type=="event" and .event.kind=="screen_summary")] | length')
AIW=$(echo "$TL" | jq '[.items[] | select(.type=="ai_work")] | length')
SC=$(echo "$RP" | jq -c .report.meta.source_counts)
check "$(echo "$SC" | jq -r .sessions)" "$SES" "source_counts.sessions matches timeline"
check "$(echo "$SC" | jq -r .activity)" "$ACT" "source_counts.activity matches timeline"
check "$(echo "$SC" | jq -r .screen_summary)" "$SCR" "source_counts.screen_summary matches timeline"
check "$(echo "$SC" | jq -r .ai_work)" "$AIW" "source_counts.ai_work matches timeline"
ACTLINE=$(echo "$RP" | jq -r .report.text | grep '^- activity: ')
check "$ACTLINE" "- activity: $ACT" "fake markdown lists material counts"
RP2=$(req POST /v1/reports/daily "{\"date\":\"${DAY}\"}")
check "$(echo "$RP2" | jq -r .generated)" "false" "re-POST without force is idempotent"
RP3=$(req POST /v1/reports/daily "{\"date\":\"${DAY}\",\"force\":true}")
check "$(echo "$RP3" | jq -r .generated)" "true" "force=true regenerates"
check "$(echo "$RP3" | jq -r .report.id)" "$RID" "force keeps the same uuid5 id"
G=$(req GET "/v1/reports/daily?date=${DAY}")
check "$(echo "$G" | jq -r .report.id)" "$RID" "GET reports/daily returns the saved report"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/v1/reports/daily?date=$(date -d '+30 day' +%F)")
check "$CODE" "404" "GET missing date -> 404"
# report started_at is day-start in tz (JST midnight = previous day 15:00Z),
# so query the list window in JST terms
RF=$(date -u -d "${DAY}T00:00:00+09:00" +%Y-%m-%dT%H:%M:%SZ)
RT=$(date -u -d "${D2}T00:00:00+09:00" +%Y-%m-%dT%H:%M:%SZ)
L=$(req GET "/v1/reports?from=${RF}&to=${RT}")
check "$(echo "$L" | jq '.events | length')" "1" "reports list shows the one report"
check "$(echo "$L" | jq -r '.events[0].kind')" "report" "reports list filters kind=report"
check "$(echo "$L" | jq -r '.events[0].id')" "$RID" "reports list returns the uuid5 report"

echo "=== 18. daily report: LLM unreachable -> 502, server keeps running"
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=openai_compat LLM_BASE_URL=http://10.255.255.1:1/v1 \
     LLM_TIMEOUT_SEC=5 REPORT_LLM_TIMEOUT_SEC=5 SESSION_JUDGE_INTERVAL=600 IDLE_MINUTES=15 \
     AM_BASE_URL=http://fake-agent-memory:8377 AM_IMPORT_INTERVAL=3 \
     "${DC[@]}" up -d api >/dev/null 2>&1; then
  bad "recreate api with unreachable LLM"
fi
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && break
  sleep 1
done
echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && ok "api recreated with unreachable LLM" || bad "api did not come back: $HZ"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/reports/daily" \
  -H 'Content-Type: application/json' -d "{\"date\":\"${D2}\",\"force\":true}")
check "$CODE" "502" "report generation reports 502 while LLM unreachable"
check "$(curl -s "$BASE/healthz" | jq -r .ok)" "true" "server stays up when report LLM fails"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/v1/reports/daily?date=${D2}")
check "$CODE" "404" "failed generation stores nothing"
G=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/v1/reports/daily?date=${DAY}")
check "$G" "200" "existing report untouched by the failure"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/blog/generate" \
  -H 'Content-Type: application/json' -d "{\"date\":\"${D2}\",\"force\":true}")
check "$CODE" "502" "blog generation reports 502 while LLM unreachable"
check "$(curl -s "$BASE/healthz" | jq -r .ok)" "true" "server stays up when blog LLM fails"
N=$(req GET "/v1/blog/posts" | jq '.posts | length')
check "$N" "0" "failed blog generation stores nothing"

echo "=== 19. blog (fake): both types, idempotent, force, list/detail/tags"
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=fake SESSION_JUDGE_INTERVAL=600 IDLE_MINUTES=15 \
     BLOG_AUTO=publish \
     AM_BASE_URL=http://fake-agent-memory:8377 AM_IMPORT_INTERVAL=3 \
     "${DC[@]}" up -d api >/dev/null 2>&1; then
  bad "recreate api with fake providers (blog)"
fi
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && break
  sleep 1
done
echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && ok "api back on fake providers (blog)" || bad "api did not come back: $HZ"
BG=$(req POST /v1/blog/generate "{\"date\":\"${DAY}\"}")
check "$(echo "$BG" | jq -r .summary.generated)" "true" "summary post generated (fake provider)"
SUMID=$(python3 -c "import uuid;print(uuid.uuid5(uuid.NAMESPACE_URL,'minecontext:sync-server:blog-post:${DAY}:summary:'))")
check "$(echo "$BG" | jq -r .summary.post.id)" "$SUMID" "summary id is uuid5(date+type+topic)"
check "$(echo "$BG" | jq -r .summary.post.slug)" "${DAY}-summary" "summary slug"
check "$(echo "$BG" | jq -r .summary.post.type)" "summary" "summary type"
check "$(echo "$BG" | jq -r .summary.post.model)" "fake" "summary model=fake"
check "$(echo "$BG" | jq -r .summary.post.status)" "published" "BLOG_AUTO=publish publishes generated posts"
check "$(echo "$BG" | jq -r .summary.post.author)" "auto" "generated post author=auto"
TL=$(req GET "/v1/timeline?date=${DAY}&tz=Asia/Tokyo")
AIW=$(echo "$TL" | jq '[.items[] | select(.type=="ai_work")] | length')
MATS=$(echo "$TL" | jq '[.items[] | select(.type=="event" and (.event.kind=="activity" or .event.kind=="screen_summary"))] | length')
AILINE=$(echo "$BG" | jq -r .summary.post.markdown | grep '^- ai_work: ')
check "$AILINE" "- ai_work: $AIW" "fake summary lists material counts"
check "$(echo "$BG" | jq '.summary.post.source_event_ids | length')" "$((AIW+MATS))" "summary source_event_ids covers material events"
LID=$(python3 -c "import uuid;print(uuid.uuid5(uuid.NAMESPACE_URL,'minecontext:sync-server:blog-post:${DAY}:learning:t1'))")
check "$(echo "$BG" | jq '.learning | length')" "1" "one learning post from the qualifying solution record"
check "$(echo "$BG" | jq -r '.learning[0].post.slug')" "${DAY}-learning-t1" "learning slug"
check "$(echo "$BG" | jq -r '.learning[0].post.id')" "$LID" "learning id is uuid5"
check "$(echo "$BG" | jq -r '.learning[0].post.source_event_ids | index("00000000-0000-4000-8000-00000000a002") != null')" "true" "learning cites the solution record (a002)"
check "$(echo "$BG" | jq -r '.learning[0].post.markdown | contains("## 参考")')" "true" "learning uses the fixed section structure"
BG2=$(req POST /v1/blog/generate "{\"date\":\"${DAY}\"}")
check "$(echo "$BG2" | jq -r .summary.generated)" "false" "re-POST without force is idempotent (summary)"
check "$(echo "$BG2" | jq -r .summary.post.id)" "$SUMID" "idempotent re-POST keeps the uuid5 id"
check "$(echo "$BG2" | jq -r '[.learning[].generated] | join(",")')" "false" "re-POST keeps learning as-is"
BG3=$(req POST /v1/blog/generate "{\"date\":\"${DAY}\",\"force\":true}")
check "$(echo "$BG3" | jq -r .summary.generated)" "true" "force=true regenerates the summary"
check "$(echo "$BG3" | jq -r .summary.post.id)" "$SUMID" "force keeps the same uuid5 id (summary)"
check "$(echo "$BG3" | jq '.learning | length')" "1" "force generates learning while preserving the published set"
BGD=$(req POST /v1/blog/generate "{\"date\":\"${D2}\"}")
check "$(echo "$BGD" | jq -r .summary.generated)" "true" "D2 summary generated"
check "$(echo "$BGD" | jq '.learning | length')" "0" "no qualifying material -> 0 learning posts"
LP=$(req GET "/v1/blog/posts")
check "$(echo "$LP" | jq '.posts | length')" "4" "list shows summary posts and both published learning versions"
check "$(echo "$LP" | jq -r '.posts[0].slug')" "${D2}-summary" "list is newest-first"
check "$(echo "$(req GET '/v1/blog/posts?type=learning')" | jq '.posts | length')" "2" "type filter"
check "$(echo "$(req GET "/v1/blog/posts?q=${D2}")" | jq '.posts | length')" "1" "q filter (title partial match)"
check "$(echo "$(req GET "/v1/blog/posts?q=${DAY}")" | jq '.posts | length')" "3" "q filter matches title/markdown"
check "$(echo "$(req GET '/v1/blog/posts?tag=e2e')" | jq '.posts | length')" "2" "tag filter"
P1=$(req GET "/v1/blog/posts?limit=3")
CUR=$(echo "$P1" | jq -r .next_cursor)
[ "$CUR" != "null" ] && ok "list cursor present at page boundary" || bad "list cursor missing"
P2=$(req GET "/v1/blog/posts?limit=3&cursor=$CUR")
check "$(echo "$P2" | jq '.posts | length')" "1" "cursor second page has the rest"
check "$(echo "$P2" | jq -r .next_cursor)" "null" "last page has no cursor"
D=$(req GET "/v1/blog/posts/${DAY}-summary")
check "$(echo "$D" | jq -r '.markdown | contains("## 概要（fake）")')" "true" "detail returns full markdown"
check "$(echo "$D" | jq -r .prev)" "null" "oldest post has no prev"
check "$(echo "$D" | jq -r .next.slug)" "${DAY}-learning-t1" "next points at the newer learning post"
DD=$(req GET "/v1/blog/posts/${D2}-summary")
check "$(echo "$DD" | jq -r .prev.slug)" "$(echo "$BG3" | jq -r '.learning[0].post.slug')" "prev points at the newer learning version"
check "$(echo "$DD" | jq -r .next)" "null" "newest post has no next"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/v1/blog/posts/does-not-exist")
check "$CODE" "404" "unknown slug -> 404"
TG=$(req GET "/v1/blog/tags")
check "$(echo "$TG" | jq -r '.[] | select(.tag=="fake") | .count')" "4" "tag counts (fake on all posts)"
check "$(echo "$TG" | jq -r '.[] | select(.tag=="e2e") | .count')" "2" "tag counts (e2e on learning)"

echo "=== 20. OpenAI-compatible /v1/embeddings relay (fake provider)"
EM=$(req POST /v1/embeddings '{"model":"qwen3-embedding-0.6b","input":"四半期財務レポート"}')
check "$(echo "$EM" | jq -r .object)" "list" "embeddings: object=list"
check "$(echo "$EM" | jq '.data | length')" "1" "embeddings: single string -> 1 vector"
check "$(echo "$EM" | jq -r .data[0].index)" "0" "embeddings: index 0"
check "$(echo "$EM" | jq '.data[0].embedding | length')" "1024" "embeddings: vector is EMBED_DIM (1024)"
check "$(echo "$EM" | jq -r .model)" "fake-embed" "embeddings: echoes server EMBED_MODEL (request model ignored)"
check "$(echo "$EM" | jq '.usage.prompt_tokens >= 1')" "true" "embeddings: usage present"
V1=$(echo "$EM" | jq -c .data[0].embedding)
EM2=$(req POST /v1/embeddings '{"model":"m","input":["四半期財務レポート","english text"],"input_type":"query"}')
check "$(echo "$EM2" | jq -r '[.data[].index] | join(",")')" "0,1" "embeddings: batch keeps input order (indexes)"
check "$(echo "$EM2" | jq -c .data[0].embedding)" "$V1" "embeddings: same text -> same vector (deterministic)"
check "$(echo "$EM2" | jq -c '.data[0].embedding != .data[1].embedding')" "true" "embeddings: different texts differ"
IN64=$(python3 -c "print(','.join(['\"x\"']*64))")
IN65=$(python3 -c "print(','.join(['\"x\"']*65))")
EM64=$(req POST /v1/embeddings "{\"model\":\"m\",\"input\":[$IN64]}")
check "$(echo "$EM64" | jq '.data | length')" "64" "embeddings: 64 inputs accepted (max)"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/embeddings" \
  -H 'Content-Type: application/json' -d "{\"model\":\"m\",\"input\":[$IN65]}")
check "$CODE" "422" "embeddings: 65 inputs -> 422"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/embeddings" \
  -H 'Content-Type: application/json' -d '{"model":"m","input":""}')
check "$CODE" "422" "embeddings: empty input -> 422"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/embeddings" \
  -H 'Content-Type: application/json' -d '{"model":"m","input":"x","input_type":"bogus"}')
check "$CODE" "422" "embeddings: bad input_type -> 422"

echo "=== 21. blog authoring (P5-B): materials, draft/publish/archive, MCP"
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=fake SESSION_JUDGE_INTERVAL=600 IDLE_MINUTES=15 \
     AM_BASE_URL=http://fake-agent-memory:8377 AM_IMPORT_INTERVAL=3 \
     "${DC[@]}" up -d api >/dev/null 2>&1; then
  bad "recreate api with fake providers (authoring)"
fi
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && break
  sleep 1
done
echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && ok "api back on fake providers (authoring)" || bad "api did not come back: $HZ"
# BLOG_AUTO default (draft): generated posts land in drafts, not the public list
BGD3=$(req POST /v1/blog/generate "{\"date\":\"${D3}\"}")
check "$(echo "$BGD3" | jq -r .summary.post.status)" "draft" "BLOG_AUTO default draft: generated post is a draft"
check "$(echo "$BGD3" | jq -r .summary.post.author)" "auto" "generated post author=auto (default mode)"
check "$(echo "$(req GET '/v1/blog/posts')" | jq '[.posts[].slug] | index("'"$(echo "$BGD3" | jq -r .summary.post.slug)"'") != null')" "false" "draft summary hidden from the default list"
check "$(echo "$(req GET "/v1/blog/posts?status=draft")" | jq '[.posts[].slug] | index("'"$(echo "$BGD3" | jq -r .summary.post.slug)"'") != null')" "true" "draft summary visible with ?status=draft"
# materials endpoint: counts line up with the timeline, report body included
TL=$(req GET "/v1/timeline?date=${DAY}&tz=Asia/Tokyo")
MT=$(req GET "/v1/blog/materials?date=${DAY}")
check "$(echo "$MT" | jq -r '.daily_report != null')" "true" "materials include the daily report body"
check "$(echo "$MT" | jq '.counts.sessions')" "$(echo "$TL" | jq '.session_totals | length')" "materials counts.sessions matches timeline"
check "$(echo "$MT" | jq '.counts.ai_work')" "$(echo "$TL" | jq '[.items[] | select(.type=="ai_work")] | length')" "materials counts.ai_work matches timeline"
check "$(echo "$MT" | jq '(.own_records | length) == (.counts.activity + .counts.screen_summary)')" "true" "materials own_records cover activity+screen_summary"
check "$(echo "$MT" | jq -r '.sessions[0] | has("minutes") and has("title")')" "true" "materials sessions carry title+minutes"
MT1=$(req GET "/v1/blog/materials?date=${DAY}&max_records=1")
check "$(echo "$MT1" | jq -r .counts.own_records_truncated)" "true" "materials max_records caps and flags truncation"
# create a draft via the authoring API (secrets get masked)
CB=$(jq -n --arg d "$DAY" '{title:"AIが書いた記事",markdown:"## 導入\n\n認証トークンは Bearer abcdefghijklmnopqrstuvwxyz012345 を使わない。\n\n本文。",date:$d,type:"post",tags:["e2e","agent"],author:"agent:claude"}')
NEW=$(req POST /v1/blog/posts "$CB")
SLUG=$(echo "$NEW" | jq -r .slug)
check "$(echo "$SLUG" | grep -cE "^${DAY}-[a-z0-9]{8}$")" "1" "created post slug is date + short alnum id"
check "$(echo "$NEW" | jq -r .status)" "draft" "created post starts as a draft"
check "$(echo "$NEW" | jq -r .author)" "agent:claude" "created post author kept"
check "$(echo "$NEW" | jq -r .type)" "post" "created post type=post"
check "$(echo "$NEW" | jq -r '.markdown | contains("Bearer ***")')" "true" "bearer token masked on create"
check "$(echo "$NEW" | jq -r '.markdown | contains("abcdefghijklmnopqrstuvwxyz012345")')" "false" "masked token not stored"
check "$(echo "$NEW" | jq -r '.excerpt | length >= 1')" "true" "excerpt auto-generated when absent"
check "$(echo "$(req GET '/v1/blog/posts')" | jq '[.posts[].slug] | index("'"$SLUG"'") != null')" "false" "draft hidden from the public list"
check "$(echo "$(req GET '/v1/blog/posts?status=draft')" | jq '[.posts[].slug] | index("'"$SLUG"'") != null')" "true" "draft visible with ?status=draft"
# PATCH / publish / unpublish
UP=$(req PATCH "/v1/blog/posts/$SLUG" '{"title":"AIが書いた記事（改）","tags":["e2e"]}')
check "$(echo "$UP" | jq -r .title)" "AIが書いた記事（改）" "PATCH updates title"
check "$(echo "$UP" | jq -r '.tags | join(",")')" "e2e" "PATCH updates tags"
check "$(echo "$UP" | jq -r .status)" "draft" "PATCH keeps draft"
PB=$(req POST "/v1/blog/posts/$SLUG/publish")
check "$(echo "$PB" | jq -r .status)" "published" "publish -> published"
check "$(echo "$(req GET '/v1/blog/posts')" | jq '[.posts[].slug] | index("'"$SLUG"'") != null')" "true" "published post in the public list"
UB=$(req POST "/v1/blog/posts/$SLUG/unpublish")
check "$(echo "$UB" | jq -r .status)" "draft" "unpublish -> draft"
check "$(echo "$(req GET '/v1/blog/posts')" | jq '[.posts[].slug] | index("'"$SLUG"'") != null')" "false" "unpublished post leaves the public list"
# rewrite: proposal only, nothing saved
RW=$(req POST "/v1/blog/posts/$SLUG/rewrite" '{"instruction":"もう少しカジュアルに"}')
check "$(echo "$RW" | jq -r '.markdown | contains("fake rewrite")')" "true" "rewrite (fake) returns a rewritten markdown"
check "$(echo "$RW" | jq -r .title)" "$(echo "$UP" | jq -r .title)" "rewrite keeps the title (fake)"
check "$(req GET "/v1/blog/posts/$SLUG?status=draft" | jq -r '.markdown | contains("fake rewrite")')" "false" "rewrite does not save"
# DELETE archives only
DEL=$(req DELETE "/v1/blog/posts/$SLUG")
check "$(echo "$DEL" | jq -r .status)" "archived" "DELETE archives the post"
check "$(echo "$(req GET '/v1/blog/posts?status=archived')" | jq '[.posts[].slug] | index("'"$SLUG"'") != null')" "true" "archived post visible with ?status=archived"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/v1/blog/posts/$SLUG")
check "$CODE" "404" "archived post hidden from the default detail view"
check "$(req DELETE "/v1/blog/posts/$SLUG" | jq -r .status)" "archived" "second DELETE is idempotent"
# MCP: initialize -> tools/list -> hibi_create_draft -> hibi_publish_post
mcprpc() {
  curl -s -X POST "$BASE/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -d "$1"
}
INIT=$(mcprpc '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"e2e","version":"1.0"}}}')
check "$(echo "$INIT" | jq -r .jsonrpc)" "2.0" "MCP initialize responds over /mcp"
check "$(echo "$INIT" | jq -r .result.serverInfo.name)" "hibi" "MCP serverInfo.name=hibi"
TOOLS=$(mcprpc '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
check "$(echo "$TOOLS" | jq '.result.tools | length')" "12" "MCP tools/list returns 12 tools"
check "$(echo "$TOOLS" | jq -r '[.result.tools[].name] | index("hibi_create_draft") != null')" "true" "MCP tool list includes hibi_create_draft"
check "$(echo "$TOOLS" | jq -r '[.result.tools[].name] | index("hibi_publish_post") != null')" "true" "MCP tool list includes hibi_publish_post"
CALL=$(jq -n --arg d "$DAY" '{jsonrpc:"2.0",id:3,method:"tools/call",params:{name:"hibi_create_draft",arguments:{title:"MCP経由の下書き",markdown:"## MCP\n\n下書きを作る。",date:$d,author_name:"claude"}}}')
MC=$(mcprpc "$CALL")
MSLUG=$(echo "$MC" | jq -r '.result.structuredContent.slug // (.result.content[0].text | fromjson | .slug)')
check "$(echo "$MSLUG" | grep -cE "^${DAY}-[a-z0-9]{8}$")" "1" "MCP hibi_create_draft returns a slug"
MP=$(req GET "/v1/blog/posts/$MSLUG?status=draft")
check "$(echo "$MP" | jq -r .author)" "agent:claude" "MCP-created draft author=agent:<name>"
check "$(echo "$MP" | jq -r .status)" "draft" "MCP-created post is a draft"
MC2=$(mcprpc "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"hibi_publish_post\",\"arguments\":{\"slug\":\"$MSLUG\"}}}")
check "$(echo "$MC2" | jq -r '.result.structuredContent.status // (.result.content[0].text | fromjson | .status)')" "published" "MCP hibi_publish_post publishes the draft"
check "$(echo "$(req GET '/v1/blog/posts')" | jq '[.posts[].slug] | index("'"$MSLUG"'") != null')" "true" "MCP-published post in the public list"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"no_such_tool","arguments":{}}}')
check "$CODE" "200" "MCP unknown tool answers JSON-RPC (error inside), HTTP stays 200"
check "$(curl -s "$BASE/healthz" | jq -r .ok)" "true" "server stays up after MCP calls"

echo "=== 22. todos: manual create/list/patch/delete, auto creation disabled + fake auto time tracking"
# TODO_JUDGE_INTERVAL=3: the matching job runs between the steps below.
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=fake SESSION_JUDGE_INTERVAL=600 IDLE_MINUTES=15 \
     TODO_JUDGE_INTERVAL=3 \
     "${DC[@]}" up -d api >/dev/null 2>&1; then
  bad "recreate api with fake providers (todos)"
fi
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && break
  sleep 1
done
echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && ok "api back on fake providers (todos)" || bad "api did not come back: $HZ"
# create (defaults + fields)
T1=$(req POST /v1/todos '{"title":"quokka-pad の仕様を書く","note":"下書きから","urgency":"high","due_date":"2026-10-20"}')
TID1=$(echo "$T1" | jq -r .id)
check "$(echo "$T1" | jq -r .status)" "open" "todo create -> open"
check "$(echo "$T1" | jq -r .source)" "me" "todo create source=me (default)"
check "$(echo "$T1" | jq -r .urgency)" "high" "todo create urgency kept"
check "$(echo "$T1" | jq -r .due_date)" "2026-10-20" "todo create due_date kept"
check "$(echo "$T1" | jq -r .deduped)" "false" "todo create not deduped"
check "$(echo "$T1" | jq '.tracked_minutes == 0')" "true" "new todo has 0 tracked minutes"
T2=$(req POST /v1/todos '{"title":"plover 機能の棚卸しをする"}')
TID2=$(echo "$T2" | jq -r .id)
check "$(echo "$T2" | jq -r .urgency)" "medium" "urgency defaults to medium"
T3=$(req POST /v1/todos '{"title":"stoat メモの掃除","urgency":"low"}')
TID3=$(echo "$T3" | jq -r .id)
T4=$(req POST /v1/todos '{"title":"quokka-pad の発表スライド","urgency":"high"}')
TID4=$(echo "$T4" | jq -r .id)
# list open: urgency high->low, newest first within the same urgency
L=$(req GET "/v1/todos?status=open")
check "$(echo "$L" | jq -r '[.todos[].id] | join(",")')" "$TID4,$TID1,$TID2,$TID3" "open list: urgency desc, then newest"
check "$(req GET "/v1/todos" | jq '.todos | length')" "4" "default status=open"
# auto creation is rejected for both near-duplicate and distinct titles
D=$(curl -s -w '\n%{http_code}' -X POST "$BASE/v1/todos" \
  -H 'Content-Type: application/json' -d '{"title":"quokka-pad の仕様を書く（再掲）","source":"auto"}')
check "${D##*$'\n'}" "422" "auto near-duplicate title -> 422"
check "$(printf '%s' "${D%$'\n'*}" | jq -r .detail)" "auto-created to-dos are disabled" "auto creation returns disabled message"
D=$(curl -s -w '\n%{http_code}' -X POST "$BASE/v1/todos" \
  -H 'Content-Type: application/json' -d '{"title":"まったく別のねこの餌やり当番","source":"auto"}')
check "${D##*$'\n'}" "422" "auto distinct title -> 422"
D=$(req POST /v1/todos '{"title":"quokka-pad の仕様を書く"}')
check "$(echo "$D" | jq -r .deduped)" "false" "source=me never dedups"
if [ "$(echo "$D" | jq -r .id)" != "$TID1" ]; then
  ok "source=me same title creates a new row"
else
  bad "source=me same title returned the existing row"
fi
# PATCH done/open toggles done_at
P=$(req PATCH "/v1/todos/$TID2" '{"status":"done"}')
check "$(echo "$P" | jq -r .status)" "done" "PATCH status=done"
check "$(echo "$P" | jq -r '.done_at != null')" "true" "done sets done_at"
check "$(req GET "/v1/todos?status=done" | jq -r "[.todos[].id] | index(\"$TID2\") != null")" "true" "done list contains it"
P=$(req PATCH "/v1/todos/$TID2" '{"status":"open"}')
check "$(echo "$P" | jq -r .status)" "open" "PATCH reopen"
check "$(echo "$P" | jq -r .done_at)" "null" "reopening clears done_at"
P=$(req PATCH "/v1/todos/$TID3" '{"urgency":"high","title":"stoat メモの大掃除"}')
check "$(echo "$P" | jq -r .urgency)" "high" "PATCH urgency"
check "$(echo "$P" | jq -r .title)" "stoat メモの大掃除" "PATCH title"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$BASE/v1/todos/$(uuid)" \
  -H 'Content-Type: application/json' -d '{"status":"done"}')
check "$CODE" "404" "PATCH unknown todo -> 404"
# DELETE archives only
DEL=$(req DELETE "/v1/todos/$TID3")
check "$(echo "$DEL" | jq -r .status)" "archived" "DELETE -> archived"
check "$(req GET '/v1/todos?status=open' | jq "[.todos[].id] | index(\"$TID3\") != null")" "false" "archived leaves the open list"
check "$(req GET '/v1/todos?status=all' | jq "[.todos[].id] | index(\"$TID3\") != null")" "true" "archived visible with status=all"
# kind=todo events are stored without creating todos
TE=$(uuid)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(mk $TE mac-1 todo "${DAY}T14:00:00+09:00" 'wombat-chart の軸ラベルを直す' 'chart fix')]}")
check "$(echo "$R" | jq -r .accepted)" "1" "kind=todo event ingested"
check "$(req GET '/v1/todos?status=all' | jq '[.todos[] | select(.title=="wombat-chart の軸ラベルを直す")] | length')" "0" "todo event does not create a todo"
WT=$(req GET '/v1/events?kind=todo' | jq -c ".events[] | select(.id==\"$TE\")")
check "$(echo "$WT" | jq -r .device_id)" "mac-1" "todo event keeps device_id"
check "$(echo "$WT" | jq -r .text)" "wombat-chart の軸ラベルを直す" "todo event text is preserved"
# fake matching: an entry appears with tracked_minutes/tracking
TT=$(req POST /v1/todos '{"title":"xenops タグの整理をする"}')
TTID=$(echo "$TT" | jq -r .id)
X1=$(uuid); X2=$(uuid)
XT1=$(date -u +%Y-%m-%dT%H:%M:%SZ)
XT2=$(date -u -d '+2 seconds' +%Y-%m-%dT%H:%M:%SZ)
XB="{\"device_id\":\"mac-1\",\"events\":[$(mk $X1 mac-1 activity "$XT1" 'xenops タグを整理している' 'tags'),$(mk $X2 mac-1 activity "$XT2" 'xenops タグの仕分けを続けた' 'tags2')]}"
R=$(req POST "/v1/events:batch" "$XB")
check "$(echo "$R" | jq -r .accepted)" "2" "matching events ingested"
ENT=""
for i in $(seq 1 20); do
  ENT=$(req GET "/v1/todos/$TTID/entries")
  [ "$(echo "$ENT" | jq '.entries | length')" -ge 1 ] 2>/dev/null && break
  sleep 1
done
check "$(echo "$ENT" | jq '.entries | length')" "1" "fake judge creates one entry"
EID=$(echo "$ENT" | jq -r '.entries[0].id')
check "$(echo "$ENT" | jq -r '.entries[0].source')" "auto" "entry source=auto"
check "$(echo "$ENT" | jq -r '.entries[0].confidence')" "0.9" "fake entry confidence 0.9"
check "$(echo "$ENT" | jq '.entries[0].minutes >= 0.5')" "true" "entry is at least 30s (min length)"
T=$(req GET "/v1/todos?status=open" | jq -c ".todos[] | select(.id==\"$TTID\")")
check "$(echo "$T" | jq '.tracked_minutes >= 0.5')" "true" "tracked_minutes reflects the entry"
check "$(echo "$T" | jq -r .tracking)" "true" "recent entry -> tracking=true"
check "$(echo "$T" | jq -r .last_tracked_at)" "$(echo "$ENT" | jq -r '.entries[0].ended_at')" "last_tracked_at = entry ended_at"
# continuation within the merge gap extends the previous entry
E0=$(echo "$ENT" | jq -r '.entries[0].ended_at')
X3=$(uuid)
XT3=$(date -u -d '+1 seconds' +%Y-%m-%dT%H:%M:%SZ)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(mk $X3 mac-1 activity "$XT3" 'xenops タグの残りを仕分けた' 'tags3')]}")
check "$(echo "$R" | jq -r .accepted)" "1" "continuation event ingested"
ENT2=""
for i in $(seq 1 20); do
  ENT2=$(req GET "/v1/todos/$TTID/entries")
  [ "$(echo "$ENT2" | jq '.entries | length')" = "1" ] 2>/dev/null \
    && [ "$(echo "$ENT2" | jq -r '.entries[0].ended_at')" != "$E0" ] && break
  sleep 1
done
check "$(echo "$ENT2" | jq '.entries | length')" "1" "continuation within gap extends (no new entry)"
check "$(echo "$ENT2" | jq -r '.entries[0].id')" "$EID" "extended entry keeps its id"
check "$(echo "$ENT2" | jq -r '.entries[0].ended_at > "'$E0'"')" "true" "ended_at was extended"
# timeline carries the day's todo entries
TL=$(req GET "/v1/timeline?date=${DAY}&tz=Asia/Tokyo")
TLE=$(echo "$TL" | jq -c ".todo_entries[] | select(.todo_id==\"$TTID\")")
check "$(echo "$TL" | jq '.todo_entries | length >= 1')" "true" "timeline has todo_entries"
check "$(echo "$TLE" | jq -r .todo_title)" "xenops タグの整理をする" "timeline todo entry carries the title"
check "$(echo "$TLE" | jq '.minutes >= 0.5')" "true" "timeline todo entry minutes"
# below TODO_MATCH_THRESHOLD (fake is 0.9 < 0.95) nothing is recorded
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=fake SESSION_JUDGE_INTERVAL=600 IDLE_MINUTES=15 \
     TODO_JUDGE_INTERVAL=3 TODO_MATCH_THRESHOLD=0.95 \
     "${DC[@]}" up -d api >/dev/null 2>&1; then
  bad "recreate api with a high todo threshold"
fi
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && break
  sleep 1
done
echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && ok "api back with a high todo threshold" || bad "api did not come back: $HZ"
PT=$(req POST /v1/todos '{"title":"potoroo メモを分類する"}')
PTID=$(echo "$PT" | jq -r .id)
P1=$(uuid)
PT1=$(date -u +%Y-%m-%dT%H:%M:%SZ)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(mk $P1 mac-1 activity "$PT1" 'potoroo メモを分類している' 'sort')]}")
check "$(echo "$R" | jq -r .accepted)" "1" "below-threshold event ingested"
sleep 8
check "$(req GET "/v1/todos/$PTID/entries" | jq '.entries | length')" "0" "below threshold -> no entry"
T=$(req GET "/v1/todos?status=open" | jq -c ".todos[] | select(.id==\"$PTID\")")
check "$(echo "$T" | jq '.tracked_minutes == 0')" "true" "below threshold -> tracked_minutes stays 0"
check "$(echo "$T" | jq -r .tracking)" "false" "below threshold -> not tracking"
# entry delete (undo a wrong entry)
R=$(req DELETE "/v1/todos/$TTID/entries/$EID")
check "$R" '{"ok":true}' "entry delete returns ok"
check "$(req GET "/v1/todos/$TTID/entries" | jq '.entries | length')" "0" "deleted entry gone"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/v1/todos/$TTID/entries/$EID")
check "$CODE" "404" "second entry delete -> 404"
T=$(req GET "/v1/todos?status=open" | jq -c ".todos[] | select(.id==\"$TTID\")")
check "$(echo "$T" | jq '.tracked_minutes == 0')" "true" "tracked_minutes back to 0 after delete"
check "$(echo "$T" | jq -r .tracking)" "false" "tracking back to false after delete"
# MCP todo tools: list / add / complete
TOOLS=$(mcprpc '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
check "$(echo "$TOOLS" | jq -r '[.result.tools[].name] | index("hibi_list_todos") != null')" "true" "MCP tool list includes hibi_list_todos"
check "$(echo "$TOOLS" | jq -r '[.result.tools[].name] | index("hibi_add_todo") != null')" "true" "MCP tool list includes hibi_add_todo"
check "$(echo "$TOOLS" | jq -r '[.result.tools[].name] | index("hibi_complete_todo") != null')" "true" "MCP tool list includes hibi_complete_todo"
MC=$(mcprpc '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"hibi_list_todos","arguments":{"status":"open"}}}')
MTL=$(echo "$MC" | jq -r '.result.structuredContent.todos // (.result.content[0].text | fromjson | .todos)')
check "$(echo "$MTL" | jq 'length >= 1')" "true" "MCP hibi_list_todos returns todos"
CALL=$(jq -nc '{jsonrpc:"2.0",id:4,method:"tools/call",params:{name:"hibi_add_todo",arguments:{title:"MCPからのToDo",urgency:"low"}}}')
MC=$(mcprpc "$CALL")
MTID=$(echo "$MC" | jq -r '.result.structuredContent.id // (.result.content[0].text | fromjson | .id)')
check "$(echo "$MTID" | grep -cE '^[0-9a-f]{8}-[0-9a-f-]{27}$')" "1" "MCP hibi_add_todo returns an id"
check "$(req GET "/v1/todos?status=open" | jq -r ".todos[] | select(.id==\"$MTID\") | .source")" "me" "MCP-added todo stored source=me"
MC=$(mcprpc "{\"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"tools/call\",\"params\":{\"name\":\"hibi_complete_todo\",\"arguments\":{\"id\":\"$MTID\"}}}")
check "$(echo "$MC" | jq -r '.result.structuredContent.status // (.result.content[0].text | fromjson | .status)')" "done" "MCP hibi_complete_todo marks done"
check "$(req GET "/v1/todos?status=done" | jq -r "[.todos[].id] | index(\"$MTID\") != null")" "true" "completed via MCP appears in the done list"
check "$(curl -s "$BASE/healthz" | jq -r .ok)" "true" "server stays up after todo section"

echo "=== 23. away: other-device offset, session/todo/stats exclusion"
# SESSION_JUDGE_INTERVAL=600: no judging races here (both test sessions are
# ended explicitly); TODO_JUDGE_INTERVAL=3: the fake matcher runs between the
# steps below. SESSION_AWAY_END_MINUTES stays at the 30min default.
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=fake SESSION_JUDGE_INTERVAL=600 IDLE_MINUTES=15 \
     TODO_JUDGE_INTERVAL=3 \
     "${DC[@]}" up -d api >/dev/null 2>&1; then
  bad "recreate api with fake providers (away)"
fi
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && break
  sleep 1
done
echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && ok "api back on fake providers (away)" || bad "api did not come back: $HZ"
awev() { # awev id from to reason (interval away record, no prose)
  printf '{"id":"%s","kind":"away","started_at":"%s","ended_at":"%s","title":"away","meta":{"reason":"%s"}}' \
    "$1" "$2" "$3" "$4"
}
n_ts() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
PD2="$(date -d '-2 day' +%F)"   # clean day: only this section's records
# away A 10:05-10:15 stands (no other device active then; the 10:06 activity
# is on the same device and must NOT cancel it). away B 11:02-11:07 is fully
# offset by win-1 activity every 2min (each event cuts +/-1min).
AW1=$(uuid); AW2=$(uuid); MA=$(uuid); WA1=$(uuid); WA2=$(uuid); WA3=$(uuid)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(awev $AW1 "${PD2}T10:05:00+09:00" "${PD2}T10:15:00+09:00" lock)]}")
check "$(echo "$R" | jq -r .accepted)" "1" "kind=away event accepted"
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(awev $AW2 "${PD2}T11:02:00+09:00" "${PD2}T11:07:00+09:00" idle),$(mk $MA mac-1 activity "${PD2}T10:06:00+09:00" '離席中に同じ端末で少し操作した' 'same-device touch')]}")
check "$(echo "$R" | jq -r .accepted)" "2" "second away + same-device activity ingested"
R=$(req POST "/v1/events:batch" "{\"device_id\":\"win-1\",\"events\":[$(mk $WA1 win-1 activity "${PD2}T11:02:00+09:00" '別の端末で予算表を直した' 'budget1'),$(mk $WA2 win-1 activity "${PD2}T11:04:00+09:00" '別の端末で予算表を書き直した' 'budget2'),$(mk $WA3 win-1 activity "${PD2}T11:06:00+09:00" '別の端末で資料の図を貼った' 'figure')]}")
check "$(echo "$R" | jq -r .accepted)" "3" "other-device activity covers the second away"
TL=$(req GET "/v1/timeline?date=${PD2}&tz=Asia/Tokyo")
check "$(echo "$TL" | jq '[.items[] | select(.type=="event" and .event.kind=="away")] | length')" "2" "timeline shows away events"
check "$(echo "$TL" | jq -r "[.items[] | select(.event.id==\"$AW1\") | .event.meta.reason] | .[0]")" "lock" "away item carries meta.reason"
check "$(echo "$TL" | jq -r '[.items[] | select(.event.kind=="away") | .event.ended_at != null] | all')" "true" "away items carry ended_at"
# sessions over both windows, ended explicitly so the judge stays out
SA=$(req POST /v1/sessions "{\"title\":\"dormouse 年表の校正\",\"device_id\":\"mac-1\",\"started_at\":\"${PD2}T10:00:00+09:00\"}")
SIDA=$(echo "$SA" | jq -r .session.id)
SE=$(req POST "/v1/sessions/$SIDA/end" "{\"ended_at\":\"${PD2}T10:20:00+09:00\"}")
check "$(echo "$SE" | jq -r .status)" "ended" "session A (10:00-10:20) ended explicitly"
SB=$(req POST /v1/sessions "{\"title\":\"dormouse 目次の点検\",\"device_id\":\"mac-1\",\"started_at\":\"${PD2}T11:00:00+09:00\"}")
SIDB=$(echo "$SB" | jq -r .session.id)
SE=$(req POST "/v1/sessions/$SIDB/end" "{\"ended_at\":\"${PD2}T11:10:00+09:00\"}")
check "$(echo "$SE" | jq -r .status)" "ended" "session B (11:00-11:10) ended explicitly"
SAR=$(req GET "/v1/sessions/$SIDA")
check "$(echo "$SAR" | jq '.duration_minutes == 10 and .away_minutes == 10')" "true" "A: 20min - 10min effective away = 10min (GET /v1/sessions)"
SBR=$(req GET "/v1/sessions/$SIDB")
check "$(echo "$SBR" | jq '.duration_minutes == 10 and .away_minutes == 0')" "true" "B: other-device activity cancels the away (all 10min kept)"
TL=$(req GET "/v1/timeline?date=${PD2}&tz=Asia/Tokyo")
TA=$(echo "$TL" | jq -c ".session_totals[] | select(.session_id==\"$SIDA\")")
check "$(echo "$TA" | jq '.minutes == 10 and .away_minutes == 10')" "true" "A: timeline session total has the same 10min + 10min away"
TB=$(echo "$TL" | jq -c ".session_totals[] | select(.session_id==\"$SIDB\")")
check "$(echo "$TB" | jq '.minutes == 10 and .away_minutes == 0')" "true" "B: timeline session total keeps all 10min"
check "$(echo "$TL" | jq '.session_totals | length')" "2" "timeline lists only the two sessions on that day"
ST=$(req GET "/v1/stats/daily?from=${PD2}&to=${PD2}")
D0=$(echo "$ST" | jq -c '.days[0]')
check "$(echo "$D0" | jq -r '[.events,.mine,.ai_work] | @tsv')" "6	4	0" "away events count as events but not mine/ai_work"
check "$(echo "$D0" | jq '.away_minutes == 10')" "true" "stats away_minutes: A's 10min counted, B's offset"
check "$(echo "$D0" | jq '.active_minutes == 10')" "true" "stats active_minutes drops the away part (15-5)"
check "$(echo "$D0" | jq '.session_minutes == 20')" "true" "stats session_minutes = 10+10 (same as sessions/timeline)"
check "$(echo "$D0" | jq -r .first_at)" "${PD2}T01:05:00Z" "first_at is the first away start (10:05 JST)"
# fake todo matcher: an entry never lands inside an away on the recording
# device, and the away blocks the merge so the work splits into two entries
NT=$(req POST /v1/todos '{"title":"numbat 索引を整える"}')
NTID=$(echo "$NT" | jq -r .id)
# Keep the second-precision event after a judge cursor that may have just
# advanced with microsecond precision (as in section 22's matching events).
T0E=$(date -u -d '+2 seconds' +%s)
N1=$(uuid)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(mk $N1 mac-1 activity "$(n_ts $T0E)" 'numbat の索引を整えている' 'index')]}")
check "$(echo "$R" | jq -r .accepted)" "1" "todo event before the away ingested"
ENT=""
for i in $(seq 1 20); do
  ENT=$(req GET "/v1/todos/$NTID/entries")
  [ "$(echo "$ENT" | jq '.entries | length')" -ge 1 ] 2>/dev/null && break
  sleep 2
done
check "$(echo "$ENT" | jq '.entries | length')" "1" "first todo entry created before the away"
check "$(date -u -d "$(echo "$ENT" | jq -r '.entries[0].started_at')" +%s)" "$T0E" "first entry starts at the matching event"
check "$(date -u -d "$(echo "$ENT" | jq -r '.entries[0].ended_at')" +%s)" "$((T0E+30))" "first entry is the 30s minimum length"
AW3=$(uuid); N3=$(uuid); N4=$(uuid)
# away T0E+35..+40 (after entry 1's firm end at +30); work resumes at +45
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(awev $AW3 "$(n_ts $((T0E+35)))" "$(n_ts $((T0E+40)))" idle),$(mk $N3 mac-1 activity "$(n_ts $((T0E+45)))" 'numbat 索引の見直しをした' 'index2'),$(mk $N4 mac-1 activity "$(n_ts $((T0E+47)))" 'numbat 索引が完成した' 'index3')]}")
check "$(echo "$R" | jq -r .accepted)" "3" "away + post-away todo events ingested"
ENT2=""
for i in $(seq 1 40); do
  ENT2=$(req GET "/v1/todos/$NTID/entries")
  [ "$(echo "$ENT2" | jq '.entries | length')" -ge 2 ] 2>/dev/null && break
  sleep 2
done
SRT=$(echo "$ENT2" | jq -c '.entries | sort_by(.started_at)')
check "$(echo "$SRT" | jq 'length')" "2" "todo entry split around the away (2 entries)"
E1E=$(date -u -d "$(echo "$SRT" | jq -r '.[0].ended_at')" +%s)
E2S=$(date -u -d "$(echo "$SRT" | jq -r '.[1].started_at')" +%s)
if [ "$E1E" -le $((T0E+35)) ] && [ "$E2S" -ge $((T0E+40)) ]; then
  ok "no todo entry overlaps the away interval"
else
  bad "todo entry overlaps the away (e1end=$E1E e2start=$E2S away=$((T0E+35))-$((T0E+40)))"
fi
T=$(req GET "/v1/todos?status=open" | jq -c ".todos[] | select(.id==\"$NTID\")")
check "$(echo "$T" | jq '.tracked_minutes >= 1')" "true" "tracked_minutes sums both split entries"
TDAY=$(TZ=Asia/Tokyo date -d "@$T0E" +%F)
TL=$(req GET "/v1/timeline?date=${TDAY}&tz=Asia/Tokyo")
check "$(echo "$TL" | jq "[.todo_entries[] | select(.todo_id==\"$NTID\")] | length")" "2" "timeline carries both split todo entries"
check "$(curl -s "$BASE/healthz" | jq -r .ok)" "true" "server stays up after away section"

echo "=== 24. away end: continuous absence ends the session at its start"
# SESSION_AWAY_END_MINUTES=0.05 (3s) + SESSION_JUDGE_INTERVAL=3: the rule fires
# quickly; TODO_JUDGE_INTERVAL=600 keeps the matcher out of the way.
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=fake SESSION_JUDGE_INTERVAL=3 IDLE_MINUTES=15 \
     SESSION_AWAY_END_MINUTES=0.05 TODO_JUDGE_INTERVAL=600 \
     "${DC[@]}" up -d api >/dev/null 2>&1; then
  bad "recreate api with a short away-end threshold"
fi
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && break
  sleep 1
done
echo "$HZ" | jq -e '.ok == true' >/dev/null 2>&1 && ok "api back with a short away-end threshold" || bad "api did not come back: $HZ"
SD=$(req POST /v1/sessions '{"title":"長い離席で切れる作業","device_id":"mac-1"}')
SDID=$(echo "$SD" | jq -r .session.id)
# 30s away starting 1s after the declare: >= the 3s threshold
AWS=$(date -u -d '+1 seconds' +%Y-%m-%dT%H:%M:%SZ)
AWE=$(date -u -d '+31 seconds' +%Y-%m-%dT%H:%M:%SZ)
AW4=$(uuid)
R=$(req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$(awev $AW4 "$AWS" "$AWE" suspend)]}")
check "$(echo "$R" | jq -r .accepted)" "1" "30s away ingested during the active session"
ST=""
for i in $(seq 1 30); do
  ST=$(req GET "/v1/sessions/$SDID" | jq -r .status)
  [ "$ST" = "ended" ] && break
  sleep 1
done
check "$ST" "ended" "away >= SESSION_AWAY_END_MINUTES ends the session"
S=$(req GET "/v1/sessions/$SDID")
check "$(echo "$S" | jq -r .end_reason)" "away" "end_reason=away"
check "$(date -u -d "$(echo "$S" | jq -r .ended_at)" +%s)" "$(date -u -d "$AWS" +%s)" "ended_at = away start (not the judge time)"
check "$(echo "$S" | jq -r .confirmed)" "false" "away end leaves confirmed=false"
check "$(date -u -d "$(echo "$S" | jq -r '.end_evidence[0].started_at')" +%s)" "$(date -u -d "$AWS" +%s)" "evidence points at the away interval"
A=$(req GET /v1/sessions/active | jq -r .session)
check "$A" "null" "no active session remains"

echo
echo "results: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]
