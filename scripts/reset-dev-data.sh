#!/usr/bin/env sh
set -eu
ENV_FILE="${ENV_FILE:-.env}"
docker compose --env-file "$ENV_FILE" down -v --remove-orphans
docker compose --env-file "$ENV_FILE" up --build --detach --wait --wait-timeout 240
