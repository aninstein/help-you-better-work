#!/usr/bin/env bash
set -euo pipefail
DUET_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DUET_ROOT"
export DUET_UID="$(id -u)"
python3 - <<'CHECK_SPACE'
import shutil
if shutil.disk_usage('.').free < 8 * 1024 ** 3:
    raise SystemExit('Build needs at least 8 GiB free disk space; preserve database volumes and clear rebuildable caches first.')
CHECK_SPACE
python3 scripts/setup-employee.py
DUET_REQUESTED_PORT="${DUET_PORT:-}"
if [[ -f .env ]]; then set -a; source .env; set +a; fi
export DUET_PORT="${DUET_REQUESTED_PORT:-${DUET_PORT:-8818}}"
COMPOSE=(docker compose -p duet-employee -f compose.yaml)
DUET_MODEL_MODE="$(python3 - <<'MODEL_MODE'
import json, os
try:
    models = json.loads(os.getenv('MODEL_CONFIG_JSON') or '[]')
    if not isinstance(models, list) or any(not isinstance(m, dict) for m in models): raise ValueError()
except (ValueError, TypeError):
    raise SystemExit('MODEL_CONFIG_JSON must be a JSON array of model objects')
models = [m for m in models if m.get('enabled', True)]
primary = os.getenv('PRIMARY_MODEL')
model = next((m for m in models if primary in (m.get('model'), m.get('model_label'))), models[0] if models else {})
print('external' if os.getenv('DEEPSEEK_OFFICIAL_API_KEY') or models and model.get('provider') != 'ollama' else 'local')
MODEL_MODE
)"
if [[ "$DUET_MODEL_MODE" == external ]]; then COMPOSE+=(-f compose.external.yaml); fi
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" up -d --build --wait web agent-worker projection-worker
printf '%s\n' "Duet is ready at http://127.0.0.1:${DUET_PORT:-8818}" 'Sign in with the administrator login and password entered during first deployment.'
