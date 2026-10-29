#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose -p duet-employee -f compose.yaml -f compose.external.yaml down
