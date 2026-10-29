#!/usr/bin/env bash
set -euo pipefail
DUET_VOICE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DUET_VOICE_ROOT"
# Speech credentials are runtime environment settings; no image/model rebuild is needed.
docker compose -p duet-employee -f compose.yaml config --quiet
docker compose -p duet-employee -f compose.yaml up -d --no-build --no-deps --wait web
printf '%s\n' 'Voice configuration reloaded. Existing records and Agent tasks are preserved.'
