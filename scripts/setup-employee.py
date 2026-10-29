"""Create private deployment secrets and collect the initial administrator login."""

import getpass
import json
import os
import re
import secrets
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = root / ".runtime" / "secrets"
folder.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(folder, 0o700)
admin_folder = root / ".runtime" / "admin-credentials"
admin_folder.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(admin_folder, 0o700)
bootstrap_folder = root / ".runtime" / "bootstrap"
bootstrap_folder.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(bootstrap_folder, 0o700)
bootstrap_file = bootstrap_folder / "bootstrap-admin.json"
legacy_credentials_present = any(admin_folder.glob("*.txt"))
if not bootstrap_file.exists() and not legacy_credentials_present:
    if not sys.stdin.isatty():
        raise SystemExit(
            "First deployment requires a terminal to set the administrator login and password."
        )
    login = input("Initial administrator login: ").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{2,63}", login):
        raise SystemExit("Login must be 3-64 characters: letters, digits, dot, underscore or hyphen.")
    password = getpass.getpass("Initial administrator password (12-256 characters): ")
    confirmation = getpass.getpass("Confirm administrator password: ")
    if not 12 <= len(password) <= 256 or password != confirmation:
        raise SystemExit("Passwords must match and contain 12-256 characters.")
    fd = os.open(bootstrap_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"login": login, "password": password}, stream)
        stream.write("\n")
if bootstrap_file.exists():
    os.chmod(bootstrap_file, 0o600)
for name in ("mysql-password", "mysql-root-password"):
    file = folder / name
    if not file.exists():
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(36))
    os.chmod(file, 0o600)
print("Private deployment configuration is ready.")
