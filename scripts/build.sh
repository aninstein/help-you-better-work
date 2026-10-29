#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
uv sync --project runtime --frozen --no-dev
runtime/.venv/bin/python -m compileall -q backend/app/employee
