#!/usr/bin/env bash
# Issue #5 reproduction: todo_judge_once fetches candidates with
# started_at >= cursor only, so
#  (A) an activity updated after the cursor passed it (same id re-posted
#      with an extended ended_at / changed text) and
#  (B) an offline record arriving late (started_at before the cursor)
# are never re-evaluated and the todos' tracked time stays too low.
# Modelled on e2e.sh §22 (fake LLM matcher). Own compose project + port so
# it never touches another stack. Asserts the CORRECT behaviour, so on the
# unfixed code this script FAILS — that failure is the reproduction.
# Usage: sudo ./scripts/repro_issue5.sh   (API_PORT=18799 by default)
set -u
cd "$(dirname "$0")/.."

API_PORT="${API_PORT:-18799}"
BASE="http://127.0.0.1:${API_PORT}"
DC="sudo docker compose -p hibi-e2e-issue5"
PASS=0
FAIL=0

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

mkev() { # mkev id kind started ended text title
  printf '{"id":"%s","device_id":"mac-1","kind":"%s","started_at":"%s","ended_at":"%s","text":%s,"title":%s}' \
    "$1" "$2" "$3" "$4" "$(jq -Rn --arg v "$5" '$v')" "$(jq -Rn --arg v "$6" '$v')"
}

entries_len() { req GET "/v1/todos/$1/entries" | jq '.entries | length'; }

post() { req POST "/v1/events:batch" "{\"device_id\":\"mac-1\",\"events\":[$1]}"; }

cleanup() {
  echo "--- cleanup: compose down -v"
  $DC down -v >/dev/null 2>&1
}
trap cleanup EXIT

echo "=== 1. compose up (fake providers, TODO_JUDGE_INTERVAL=3)"
$DC down -v >/dev/null 2>&1
if ! sudo env EMBED_PROVIDER=fake LLM_PROVIDER=fake \
     SESSION_JUDGE_INTERVAL=600 IDLE_MINUTES=15 TODO_JUDGE_INTERVAL=3 \
     AM_IMPORT_ENABLED=0 REPORT_AUTO_ENABLED=0 API_PORT=$API_PORT \
     docker compose -p hibi-e2e-issue5 up -d --build >/dev/null 2>&1; then
  bad "docker compose up"
  echo "results: PASS=$PASS FAIL=$FAIL"; exit 1
fi
ok "docker compose up"
HZ=""
for i in $(seq 1 60); do
  HZ=$(curl -s "$BASE/healthz" || true)
  echo "$HZ" | jq -e '.ok == true and .db == "ok"' >/dev/null 2>&1 && break
  sleep 1
done
if echo "$HZ" | jq -e '.ok == true and .db == "ok"' >/dev/null 2>&1; then
  ok "healthz ok"
else
  bad "healthz: $HZ"
fi

echo "=== 2. device + 3 todos (tapir=control, urial=update case, lynx=late case)"
req PUT /v1/devices/mac-1 '{"name":"repro5","os":"darwin"}' >/dev/null
T_TAPIR=$(req POST /v1/todos '{"title":"tapir 棚卸し表を作る"}' | jq -r .id)
T_URIAL=$(req POST /v1/todos '{"title":"urial 請求一覧を作る"}' | jq -r .id)
T_LYNX=$(req POST /v1/todos '{"title":"lynx 領収書を整理する"}' | jq -r .id)
check "$(req GET '/v1/todos?status=open' | jq '.todos | length')" "3" "3 open todos"

echo "=== 3. control: activity inside the window -> tapir entry (pipeline proof)"
A1=$(uuid)
S1=$(date -u +%Y-%m-%dT%H:%M:%SZ)
E1=$(date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%SZ)
R=$(post "$(mkev $A1 activity "$S1" "$E1" 'tapir 棚卸し表を作っている' 'repro5 a1')")
check "$(echo "$R" | jq -r .accepted)" "1" "control event ingested"
N=""
for i in $(seq 1 20); do
  N=$(entries_len $T_TAPIR)
  [ "$N" -ge 1 ] 2>/dev/null && break
  sleep 1
done
check "$N" "1" "control: tapir todo got an entry (matcher + cursor pipeline works)"
check "$(entries_len $T_URIAL)" "0" "control: urial todo has no entry yet (text does not match)"
# let at least one more cycle run so the cursor is clearly past S1
sleep 4

echo "=== 4. case A: same id re-posted (ended_at extended + text now matching urial)"
E1X=$(date -u -d '+5 minutes' +%Y-%m-%dT%H:%M:%SZ)
R=$(post "$(mkev $A1 activity "$S1" "$E1X" 'tapir 棚卸し表を作っていて、urial 請求一覧も作っている' 'repro5 a1 v2')")
check "$(echo "$R" | jq -r '"\(.accepted)/\(.updated)"')" "0/1" "same id re-post -> updated (ended_at extended, updated_at bumped)"
EV=$(req GET '/v1/events?limit=50' | jq -c ".events[] | select(.id==\"$A1\")")
check "$(echo "$EV" | jq -r .ended_at)" "$E1X" "event row carries the extended ended_at"
N=""
for i in $(seq 1 20); do
  N=$(entries_len $T_URIAL)
  [ "$N" -ge 1 ] 2>/dev/null && break
  sleep 1
done
check "$N" "1" "case A: updated activity re-evaluated -> urial entry (FR-1/FR-3/FR-5)"

echo "=== 5. case B: offline record arriving late (started_at before the cursor)"
B1=$(uuid)
S2=$(date -u -d '-60 seconds' +%Y-%m-%dT%H:%M:%SZ)
E2=$(date -u -d '-58 seconds' +%Y-%m-%dT%H:%M:%SZ)
R=$(post "$(mkev $B1 activity "$S2" "$E2" 'lynx 領収書を整理している' 'repro5 b1')")
check "$(echo "$R" | jq -r .accepted)" "1" "late record ingested"
N=""
for i in $(seq 1 20); do
  N=$(entries_len $T_LYNX)
  [ "$N" -ge 1 ] 2>/dev/null && break
  sleep 1
done
check "$N" "1" "case B: late record (started_at < cursor) evaluated -> lynx entry (FR-2/FR-4)"

echo "=== diagnostics (facts for the fix stage)"
$DC exec -T db psql -U sync -d sync -c \
  "SELECT name, cursor_ts, last_success_at FROM importer_state WHERE name='todo_judge';"
$DC exec -T db psql -U sync -d sync -c \
  "SELECT id, started_at, ended_at, created_at, updated_at FROM events WHERE kind='activity' ORDER BY started_at;"
$DC exec -T db psql -U sync -d sync -c \
  "SELECT todo_id, started_at, ended_at, source, event_ids FROM todo_time_entries ORDER BY started_at;"

echo "results: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]
