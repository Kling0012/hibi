#!/usr/bin/env bash
# Issue #5 regression tests (permanent): the four cases the fix must keep
# passing, against a disposable compose stack with fake providers.
#   A (FR-8/FR-14): an activity reposted with the same id and an extended
#      ended_at is re-evaluated; the entry grows to the new interval and
#      keeps its id (replace, not add).
#   B (FR-9/FR-15): an offline record arriving late (started_at before the
#      cursor) is still evaluated.
#   C (FR-10/FR-16): 501 events sharing one changed_at (one transaction)
#      are all processed across the 500-per-page boundary. The matching
#      event has the largest id, so only page 2 can reach it.
#   D (FR-11/FR-17): further judge cycles and an identical re-post never
#      double-add time.
# Own compose project + port so it never touches another stack. Asserts the
# CORRECT behaviour, so on the unfixed code this script FAILS.
# Usage: sudo ./scripts/regression_issue5.sh   (API_PORT=18798 by default)
set -u
cd "$(dirname "$0")/.."

API_PORT="${API_PORT:-18798}"
BASE="http://127.0.0.1:${API_PORT}"
DC="sudo docker compose -p hibi-e2e-issue5-reg"
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

total_sec() { # tracked seconds of a todo (entries carry rounded minutes)
  req GET "/v1/todos/$1/entries" | jq '[.entries[].minutes] | ((add // 0) * 60) | round'
}

wait_sec() { # wait_sec TODO SECONDS LABEL
  local t="" i
  for i in $(seq 1 40); do
    t=$(total_sec "$1")
    [ "$t" = "$2" ] 2>/dev/null && break
    sleep 1
  done
  check "$t" "$2" "$3"
}

snap() { echo "$(entries_len $1)/$(total_sec $1)"; }

PSQL() { $DC exec -T db psql -U sync -d sync -v ON_ERROR_STOP=1 "$@"; }

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
     docker compose -p hibi-e2e-issue5-reg up -d --build >/dev/null 2>&1; then
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

echo "=== 2. device + 3 todos (quokka=extension, pika=same-time page, narwhal=late)"
req PUT /v1/devices/mac-1 '{"name":"reg5","os":"darwin"}' >/dev/null
T_QUOKKA=$(req POST /v1/todos '{"title":"quokka 在庫一覧を作る"}' | jq -r .id)
T_PIKA=$(req POST /v1/todos '{"title":"pika 同時刻の記録を処理する"}' | jq -r .id)
T_NARWHAL=$(req POST /v1/todos '{"title":"narwhal 領収書を整理する"}' | jq -r .id)
check "$(req GET '/v1/todos?status=open' | jq '.todos | length')" "3" "3 open todos"

echo "=== 3. case A (FR-8/FR-14): same-id ended_at extension reaches the entry"
# Keep the activity inside one 30-minute judging window and fully in the
# past: start at :25 (or :55), so the extended end lands on the :00/:30 edge.
TIMES=$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
now = datetime.now(timezone.utc)
cs = now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0)
t0 = cs + timedelta(minutes=25)
if t0 > now - timedelta(minutes=6):
    t0 -= timedelta(minutes=30)
fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
print(fmt(t0), fmt(t0 + timedelta(seconds=60)), fmt(t0 + timedelta(seconds=300)))
PY
)
set -- $TIMES
TA0=$1; TA1=$2; TAX=$3
A1=$(uuid)
R=$(post "$(mkev $A1 activity "$TA0" "$TA1" 'quokka 在庫一覧を作っている' 'reg a1')")
check "$(echo "$R" | jq -r .accepted)" "1" "case A: initial event ingested"
wait_sec $T_QUOKKA 60 "case A: initial 1-minute entry recorded"
EID=$(req GET "/v1/todos/$T_QUOKKA/entries" | jq -r '.entries[0].id')
R=$(post "$(mkev $A1 activity "$TA0" "$TAX" 'quokka 在庫一覧を作っている' 'reg a1 v2')")
check "$(echo "$R" | jq -r '"\(.accepted)/\(.updated)"')" "0/1" "case A: same id re-post -> updated (ended_at extended)"
wait_sec $T_QUOKKA 300 "case A: extension re-evaluated to 5 minutes (FR-1/FR-3/FR-5)"
check "$(entries_len $T_QUOKKA)" "1" "case A: still one entry (replace, not add)"
check "$(req GET "/v1/todos/$T_QUOKKA/entries" | jq -r '.entries[0].id')" "$EID" \
  "case A: entry id preserved across re-evaluation"

echo "=== 4. case B (FR-9/FR-15): offline record arriving late (started_at < cursor)"
B1=$(uuid)
S2=$(date -u -d '-120 seconds' +%Y-%m-%dT%H:%M:%SZ)
E2=$(date -u -d '-60 seconds' +%Y-%m-%dT%H:%M:%SZ)
R=$(post "$(mkev $B1 activity "$S2" "$E2" 'narwhal 領収書を整理している' 'reg b1')")
check "$(echo "$R" | jq -r .accepted)" "1" "case B: late record ingested"
wait_sec $T_NARWHAL 60 "case B: late record (started_at < cursor) evaluated (FR-2/FR-4)"

echo "=== 5. case C (FR-10/FR-16): 501 events with one shared changed_at"
# events:batch caps at 500 per request, so inject 501 rows in ONE psql
# transaction: now() is the transaction timestamp, so every row gets the
# same created_at/updated_at. The only matching event has the largest id,
# so the 500-per-page cursor must advance within the shared timestamp.
if PSQL <<'SQL'
BEGIN;
INSERT INTO events (id, device_id, kind, started_at, ended_at, app, title, text, meta, source, created_at, updated_at)
SELECT ('00000000-0000-0000-0000-' || lpad(to_hex(g), 12, '0'))::uuid, 'mac-1', 'activity',
       now() - interval '70 seconds', now() - interval '10 seconds',
       'reg', 'filler', 'filler noise', '{}'::jsonb, NULL, now(), now()
FROM generate_series(1, 500) AS g;
INSERT INTO events (id, device_id, kind, started_at, ended_at, app, title, text, meta, source, created_at, updated_at)
VALUES ('ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid, 'mac-1', 'activity',
        now() - interval '70 seconds', now() - interval '10 seconds',
        'reg', 'pika', 'pika 同時刻の記録を処理している', '{}'::jsonb, NULL, now(), now());
COMMIT;
SQL
then ok "case C: 501 events inserted in one transaction"; else bad "case C: insert failed"; fi
CNT=$(PSQL -Atc "SELECT count(*) FROM events WHERE id::text LIKE '00000000-0000-0000-0000-%' OR id::text = 'ffffffff-ffff-ffff-ffff-ffffffffffff'")
check "$CNT" "501" "case C: 501 rows present"
DIST=$(PSQL -Atc "SELECT count(*) FROM (SELECT DISTINCT greatest(created_at, updated_at) FROM events WHERE id::text LIKE '00000000-0000-0000-0000-%' OR id::text = 'ffffffff-ffff-ffff-ffff-ffffffffffff') s")
check "$DIST" "1" "case C: all 501 share one changed_at"
wait_sec $T_PIKA 60 "case C: page 2 reaches the same-changed_at tail event (FR-10/FR-16)"

echo "=== 6. case D (FR-11/FR-17): reruns and identical re-post never double-add"
SQ=$(snap $T_QUOKKA); SP=$(snap $T_PIKA); SN=$(snap $T_NARWHAL)
sleep 8  # >= 2 idle judge cycles
check "$(snap $T_QUOKKA)" "$SQ" "case D: idle cycles keep quokka at $SQ"
check "$(snap $T_PIKA)" "$SP" "case D: idle cycles keep pika at $SP"
check "$(snap $T_NARWHAL)" "$SN" "case D: idle cycles keep narwhal at $SN"
R=$(post "$(mkev $A1 activity "$TA0" "$TAX" 'quokka 在庫一覧を作っている' 'reg a1 v2')")
check "$(echo "$R" | jq -r '"\(.accepted)/\(.updated)"')" "0/1" "case D: identical re-post -> updated again"
sleep 10  # let a cycle re-evaluate the identical payload
check "$(snap $T_QUOKKA)" "1/300" "case D: identical re-post keeps one 5-minute entry"
check "$(snap $T_PIKA)" "$SP" "case D: pika unchanged after re-post"
check "$(snap $T_NARWHAL)" "$SN" "case D: narwhal unchanged after re-post"

echo "--- diagnostics"
PSQL -c "SELECT name, cursor_ts, last_success_at FROM importer_state ORDER BY name;"
PSQL -c "SELECT todo_id, started_at, ended_at, source, event_ids FROM todo_time_entries ORDER BY started_at;"

echo "results: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]
