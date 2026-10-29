#!/usr/bin/env bash
# Start the isolated local demo and seed fictional data. No host .env is loaded.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
DOCKER=(docker --host unix:///var/run/docker.sock)
if ! "${DOCKER[@]}" info >/dev/null 2>&1; then
  DOCKER=(sudo -n docker --host unix:///var/run/docker.sock)
fi
DC=("${DOCKER[@]}" compose --env-file /dev/null --project-directory "$ROOT/scripts"
    -p hibi-demo -f "$ROOT/scripts/compose.demo.yml")

# Send only the Dockerfile's required inputs. In particular, never send .env,
# secrets/, local data, or the working directory as the build context.
tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' -cf - \
  Dockerfile pyproject.toml uv.lock alembic.ini alembic app |
  "${DOCKER[@]}" build --tag hibi-demo-api:local -
"${DC[@]}" up -d --wait --wait-timeout 120

python3 - <<'PY'
import json
import time
import urllib.error
import urllib.request

client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(60):
    try:
        with client.open("http://127.0.0.1:18790/healthz", timeout=3) as response:
            health = json.load(response)
        if health.get("ok") is True and health.get("db") == "ok":
            break
    except (OSError, urllib.error.URLError, ValueError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Demo API did not become ready at 127.0.0.1:18790")
PY
python3 "$ROOT/scripts/demo_seed.py"
printf '\nUI: http://127.0.0.1:18790/ui/?lang=ja\n'
printf 'Select the previous day to view the demo timeline and daily report.\n'
printf 'Stop (keep all data): %s/scripts/demo_down.sh\n' "$ROOT"
