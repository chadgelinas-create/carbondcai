#!/usr/bin/env bash
# Start embedded TimescaleDB, migrate, then serve FastAPI on Render's PORT.
set -euo pipefail

export PORT="${PORT:-10000}"
export PGDATA="${PGDATA:-/var/lib/postgresql/data}"
export POSTGRES_USER="${SKID2_DATABASE_USER:-skid2}"
export POSTGRES_PASSWORD="${SKID2_DATABASE_PASSWORD:-render-demo-only}"
export POSTGRES_DB="${SKID2_DATABASE_NAME:-skid2}"
# Build a URL-safe DSN (generated Render secrets may include special characters).
export SKID2_DATABASE_URL="$(
  python - <<'PY'
import os
from urllib.parse import quote_plus
user = os.environ["POSTGRES_USER"]
password = quote_plus(os.environ["POSTGRES_PASSWORD"])
db = os.environ["POSTGRES_DB"]
print(f"postgresql+psycopg://{user}:{password}@127.0.0.1:5432/{db}")
PY
)"

mkdir -p "$PGDATA"
chown -R postgres:postgres "$PGDATA" /var/run/postgresql 2>/dev/null || true

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "Initializing TimescaleDB cluster..."
  gosu postgres initdb -D "$PGDATA" --auth-host=scram-sha-256 --auth-local=trust
  cat >> "$PGDATA/postgresql.conf" <<'EOF'
listen_addresses = '127.0.0.1'
shared_preload_libraries = 'timescaledb'
EOF
  echo "host all all 127.0.0.1/32 scram-sha-256" >> "$PGDATA/pg_hba.conf"
fi

echo "Starting TimescaleDB..."
gosu postgres pg_ctl -D "$PGDATA" -o "-c listen_addresses=127.0.0.1" -w start

# Ensure role + database exist (idempotent).
gosu postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${POSTGRES_USER}') THEN
    CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}';
  ELSE
    ALTER ROLE ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${POSTGRES_DB}')\gexec
GRANT ALL PRIVILEGES ON DATABASE ${POSTGRES_DB} TO ${POSTGRES_USER};
SQL

gosu postgres psql -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
gosu postgres psql -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "ALTER DATABASE ${POSTGRES_DB} OWNER TO ${POSTGRES_USER};"

echo "Running migrations..."
cd /app
alembic upgrade head

echo "Starting API on 0.0.0.0:${PORT}..."
exec uvicorn skid2.backend.app:app --host 0.0.0.0 --port "${PORT}" --no-access-log
