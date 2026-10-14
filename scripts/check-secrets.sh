#!/usr/bin/env sh
set -eu
if [ ! -f .secrets.baseline ]; then
  detect-secrets scan > .secrets.baseline
fi
detect-secrets audit --report --fail-on-unverified .secrets.baseline 2>/dev/null || detect-secrets scan --baseline .secrets.baseline
