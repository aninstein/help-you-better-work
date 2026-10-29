#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
SOURCE="${1:?Usage: restore-employee.sh BACKUP_DIRECTORY --replace-current-data}"
[[ "${2:-}" == '--replace-current-data' ]] || { printf '%s\n' 'Restore replaces current database/files. Pass --replace-current-data explicitly.' >&2; exit 2; }
python3 - "$SOURCE" <<'PY'
import hashlib,json,sys,tarfile,posixpath
from pathlib import Path
p=Path(sys.argv[1]);checks=json.loads((p/'checksums.json').read_text())
if not {'mysql.sql','files.tar.gz'}.issubset(checks): raise SystemExit('Incomplete backup checksums')
for name,expected in checks.items():
 if Path(name).name!=name or hashlib.sha256((p/name).read_bytes()).hexdigest()!=expected: raise SystemExit('Backup checksum mismatch')
with tarfile.open(p/'files.tar.gz') as archive:
 for member in archive:
  normalized=posixpath.normpath(member.name)
  if normalized.startswith('/') or normalized=='..' or normalized.startswith('../') or member.isdev(): raise SystemExit('Unsafe backup archive path')
  if member.issym() or member.islnk():
   target=posixpath.normpath(posixpath.join(posixpath.dirname(normalized) if member.issym() else '',member.linkname))
   if target.startswith('/') or target=='..' or target.startswith('../'): raise SystemExit('Unsafe backup archive link')
PY
COMPOSE=(docker compose -p duet-employee -f compose.yaml -f compose.external.yaml)
"${COMPOSE[@]}" stop web agent-worker projection-worker
"${COMPOSE[@]}" exec -T mysql sh -c 'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql --binary-mode=1 -uroot' < "$SOURCE/mysql.sql"
"${COMPOSE[@]}" run --rm --no-deps --entrypoint sh web -c 'find /app/data -mindepth 1 -delete; tar xzf - -C /app/data' < "$SOURCE/files.tar.gz"
"${COMPOSE[@]}" run --rm migrate
"${COMPOSE[@]}" start web agent-worker projection-worker
printf '%s\n' 'Restore completed. Verify readiness and account/record ownership before reopening traffic.'
