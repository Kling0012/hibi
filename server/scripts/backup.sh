#!/bin/sh
# hibi-server DB backup.
#   pg_dump -Fc -> ${BACKUP_DIR}/hibi-YYYYMMDD-HHMM.dump
#   prune hibi-*.dump older than ${KEEP_DAYS} days (this directory only).
# Exits non-zero on failure (a failed backup stops only itself; the server
# keeps running).
#
# Configuration: environment variables, or the same keys in .env at the
# repository root (environment variables win).
#   COMPOSE_PROJECT_NAME  compose project (default: hibi); the DB container
#                         is expected at <project>-db-1
#   BACKUP_DIR            where dumps go (default: ./backups)
#   KEEP_DAYS             prune dumps older than this (default: 14)
set -u
cd "$(dirname "$0")/.." || exit 1

if [ -f .env ]; then
  pick() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-; }
  [ -n "${COMPOSE_PROJECT_NAME:-}" ] || COMPOSE_PROJECT_NAME="$(pick COMPOSE_PROJECT_NAME)"
  [ -n "${BACKUP_DIR:-}" ] || BACKUP_DIR="$(pick BACKUP_DIR)"
  [ -n "${KEEP_DAYS:-}" ] || KEEP_DAYS="$(pick KEEP_DAYS)"
fi

PROJECT="${COMPOSE_PROJECT_NAME:-hibi}"
DB_CONTAINER="${PROJECT}-db-1"
DIR="${BACKUP_DIR:-./backups}"
KEEP="${KEEP_DAYS:-14}"
DBUSER=sync
DBNAME=sync
ERRLOG="$(mktemp)"

log() { echo "$(date "+%F %T") [hibi-backup] $*"; }

mkdir -p "$DIR" || { log "ERROR: mkdir $DIR failed"; exit 1; }

STAMP=$(date +%Y%m%d-%H%M)
OUT="$DIR/hibi-$STAMP.dump"

log "pg_dump start ($DB_CONTAINER) -> $OUT"
if docker exec -i "$DB_CONTAINER" pg_dump -Fc -U "$DBUSER" "$DBNAME" > "$OUT.partial" 2>"$ERRLOG"; then
  mv "$OUT.partial" "$OUT"
  log "dump OK: $OUT ($(du -h "$OUT" | cut -f1))"
else
  rc=$?
  log "ERROR: pg_dump failed rc=$rc: $(tail -n 3 "$ERRLOG" 2>/dev/null)"
  rm -f "$OUT.partial" "$ERRLOG"
  exit "$rc"
fi
rm -f "$ERRLOG"

# prune hibi-*.dump older than KEEP days
find "$DIR" -maxdepth 1 -type f -name "hibi-*.dump" -mtime "+$KEEP" | while read -r f; do
  log "prune: $f"
  rm -f -- "$f"
done

log "done"
