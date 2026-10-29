#!/usr/bin/env bash
set -euo pipefail
DUET_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DUET_ROOT"
if [[ -f .env ]]; then set -a; source .env; set +a; fi
python3 scripts/setup-employee.py
if [[ ! -x runtime/.venv/bin/python || ! -f frontend/dist/index.html ]]; then ./scripts/build.sh; fi
export PYTHONPATH="$DUET_ROOT/backend"
export MYSQL_PASSWORD_FILE="$DUET_ROOT/.runtime/secrets/mysql-password"
export BOOTSTRAP_ADMIN_FILE="$DUET_ROOT/.runtime/bootstrap/bootstrap-admin.json"
runtime/.venv/bin/python -m app.employee.migrate
runtime/.venv/bin/python -m app.employee.cli bootstrap
runtime/.venv/bin/python -m app.employee.worker &
DUET_WORKER_PID=$!
AGENT_WORKER_KIND=projection runtime/.venv/bin/python -m app.employee.worker &
DUET_PROJECTION_PID=$!
trap 'kill "$DUET_WORKER_PID" "$DUET_PROJECTION_PID" 2>/dev/null || true' EXIT INT TERM
runtime/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${DUET_PORT:-8818}" &
DUET_WEB_PID=$!
trap 'kill "$DUET_WORKER_PID" "$DUET_PROJECTION_PID" "$DUET_WEB_PID" 2>/dev/null || true' EXIT INT TERM
while kill -0 "$DUET_WEB_PID" 2>/dev/null && kill -0 "$DUET_WORKER_PID" 2>/dev/null && kill -0 "$DUET_PROJECTION_PID" 2>/dev/null; do sleep 1; done
printf '%s\n' 'An employee service exited; stopping the remaining services.' >&2
exit 1
