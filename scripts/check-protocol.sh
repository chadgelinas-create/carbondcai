#!/usr/bin/env sh
set -eu
cd /workspace 2>/dev/null || cd "$(dirname "$0")/.."
uv run python -m skid2.protocol validate protocol/devices/*.yaml protocol/blocked/*.yaml
uv run python -m skid2.protocol check-drift
