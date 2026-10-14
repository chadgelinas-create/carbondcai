#!/bin/sh
set -eu

case "${1:-}" in
  migrate)
    echo "Applying database migrations..."
    exec alembic upgrade head
    ;;
  serve)
    shift
    echo "Applying database migrations before backend startup..."
    alembic upgrade head
    echo "Starting backend..."
    exec uvicorn skid2.backend.app:app "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
