#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
DESTINATION="${1:-.runtime/backups/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$DESTINATION"
chmod 700 "$DESTINATION"
umask 077
COMPOSE=(docker compose -p duet-employee -f compose.yaml -f compose.external.yaml)
"${COMPOSE[@]}" stop web agent-worker projection-worker
trap '"${COMPOSE[@]}" start web agent-worker projection-worker >/dev/null' EXIT
"${COMPOSE[@]}" exec -T mysql sh -c 'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysqldump -uroot --single-transaction --hex-blob --routines --triggers --databases duet' > "$DESTINATION/mysql.sql"
"${COMPOSE[@]}" run --rm --no-deps --entrypoint tar web czf - -C /app/data . > "$DESTINATION/files.tar.gz"
python3 - "$DESTINATION" <<'PY'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);(p/'checksums.json').write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file()},indent=2)+'\n')
PY
printf '%s\n' "Backup completed: $DESTINATION"
