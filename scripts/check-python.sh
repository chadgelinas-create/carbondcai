#!/usr/bin/env sh
set -eu
cd /workspace 2>/dev/null || cd "$(dirname "$0")/.."
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest
