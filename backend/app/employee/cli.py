"""Operator-only commands; never expose recovery actions as public endpoints."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import delete, select, update

from . import auth
from .db import audit, auth_sessions, locks, now, transaction, users


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "bootstrap",
            "worker-health",
            "legacy-inventory",
            "legacy-import",
            "recover-admin",
        ],
    )
    parser.add_argument("--source")
    parser.add_argument("--output")
    parser.add_argument("--user-id")
    args = parser.parse_args()
    if args.command == "bootstrap":
        created = auth.bootstrap()
        print(
            "Administrator initialized"
            if created
            else "Administrator bootstrap already applied; credentials and roles unchanged"
        )
    elif args.command == "worker-health":
        with transaction() as c:
            latest = c.execute(
                select(audit.c.created_at)
                .where(
                    audit.c.action
                    == (
                        "projection.worker.ready"
                        if os.getenv("AGENT_WORKER_KIND") == "projection"
                        else "worker.ready"
                    ),
                    audit.c.target_id == os.getenv("HOSTNAME", "local"),
                )
                .order_by(audit.c.created_at.desc())
                .limit(1)
            ).scalar()
            if not latest or latest < now() - 45:
                raise SystemExit(1)
    elif args.command.startswith("legacy-"):
        if not args.source or not args.output:
            parser.error("--source and --output are required")
        from .legacy import import_legacy, inventory

        report = (inventory if args.command == "legacy-inventory" else import_legacy)(args.source)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        output.chmod(0o600)
        print("Legacy report written: " + str(output))
    elif args.command == "recover-admin":
        if not args.user_id or not args.output:
            parser.error("--user-id and new --output credentials path are required")
        import secrets

        raw = "Aa9!" + secrets.token_urlsafe(24)
        target = Path(args.output)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with transaction() as c:
                c.execute(
                    select(locks).where(locks.c.name == "admin_membership").with_for_update()
                ).one()
                user = (
                    c.execute(select(users).where(users.c.id == args.user_id).with_for_update())
                    .mappings()
                    .one()
                )
                c.execute(
                    update(users)
                    .where(users.c.id == args.user_id)
                    .values(
                        password_hash=auth.password(raw),
                        must_change_password=True,
                        status="active",
                        roles=["talker", "hinter_admin"],
                        auth_version=user["auth_version"] + 1,
                        version=user["version"] + 1,
                    )
                )
                c.execute(delete(auth_sessions).where(auth_sessions.c.user_id == args.user_id))
                auth.audit_event(c, None, "operator.admin_recovery", args.user_id)
                with os.fdopen(fd, "w") as stream:
                    stream.write(f"user_id: {args.user_id}\npassword: {raw}\n")
                    stream.flush()
                    os.fsync(stream.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        print("Recovery credentials written: " + str(target))


if __name__ == "__main__":
    main()
