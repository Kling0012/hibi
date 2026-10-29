#!/usr/bin/env bash
# Stop only this demo's services. Containers, network, and database volume stay.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
DOCKER=(docker --host unix:///var/run/docker.sock)
if ! "${DOCKER[@]}" info >/dev/null 2>&1; then
  DOCKER=(sudo -n docker --host unix:///var/run/docker.sock)
fi
"${DOCKER[@]}" compose --env-file /dev/null --project-directory "$ROOT/scripts" \
  -p hibi-demo -f "$ROOT/scripts/compose.demo.yml" stop
printf 'Demo stopped. Database volume hibi-demo-db-data is retained.\n'
