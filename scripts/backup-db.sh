#!/usr/bin/env sh
# Offsite backup hook — dumps TimescaleDB to a timestamped file.
set -eu
ENV_FILE="${ENV_FILE:-.env}"
OUT_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
docker compose --env-file "$ENV_FILE" exec -T db \
  pg_dump -U "${SKID2_DATABASE_USER:-skid2}" "${SKID2_DATABASE_NAME:-skid2}" \
  > "$OUT_DIR/skid2-$STAMP.sql"
echo "wrote $OUT_DIR/skid2-$STAMP.sql"
