"""Read-only legacy inventory and replay-safe import; no automatic account claim."""

import hashlib
import json
import sqlite3
from datetime import UTC
from pathlib import Path

from sqlalchemy import insert, select

from .db import legacy_maps, memories, now, records, threads, transaction, workspaces


def stable(kind, value):
    return f"legacy_{kind}_{hashlib.sha256(value.encode()).hexdigest()[:32]}"


def source_identity(root):
    root = Path(root).resolve()
    digest = hashlib.sha256((root / "duet.db").read_bytes()).hexdigest()
    sidecar = root / "migration-source.json"
    if sidecar.exists():
        identity = json.loads(sidecar.read_text())
        if identity["sqlite_sha256"] != digest:
            raise ValueError("Legacy snapshot checksum no longer matches its identity")
        scope = identity["source_scope"]
        if len(scope) != 16 or any(c not in "0123456789abcdef" for c in scope):
            raise ValueError("Invalid legacy source identity")
        return scope
    return digest[:16]


def inventory(root):
    root = Path(root).resolve()
    db = root / "duet.db"
    result = {
        "root": str(root),
        "sqlite_exists": db.exists(),
        "talkers": 0,
        "sessions": 0,
        "wiki_files": 0,
        "binary_files": 0,
        "files": [],
        "unclaimed": [],
    }
    if db.exists():
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
            for table in ("talkers", "sessions"):
                result[table] = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            result["unclaimed"] = [r[0] for r in c.execute("SELECT id FROM talkers")]
    for file in sorted(root.rglob("*")):
        if (
            not file.is_file()
            or file.is_symlink()
            or "models" in file.relative_to(root).parts
            or file.suffix in (".db", ".db-wal", ".db-shm")
        ):
            continue
        relative = str(file.relative_to(root))
        result["files"].append(
            {
                "path": relative,
                "size": file.stat().st_size,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
        )
        result["wiki_files" if file.suffix == ".md" else "binary_files"] += 1
    return result


def import_legacy(root):
    root = Path(root).resolve()
    report = inventory(root)
    source_scope = source_identity(root)
    with sqlite3.connect(f"file:{root / 'duet.db'}?mode=ro", uri=True) as db:
        talkers = [json.loads(r[0]) for r in db.execute("SELECT state FROM talkers")]
        sessions = [json.loads(r[0]) for r in db.execute("SELECT state FROM sessions")]

    def mapping(c, key, target, payload):
        key = source_scope + ":" + hashlib.sha256(key.encode()).hexdigest()
        checksum = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        old = (
            c.execute(select(legacy_maps).where(legacy_maps.c.source_key == key)).mappings().first()
        )
        if old:
            if old["checksum"] != checksum:
                raise ValueError("Legacy source changed after import; use a new immutable snapshot")
            return False
        c.execute(
            insert(legacy_maps).values(
                source_key=key,
                target_id=target,
                checksum=checksum,
                payload=payload,
                created_at=now(),
            )
        )
        return True

    def scoped(value):
        return source_scope + ":" + str(value)

    with transaction() as c:
        for talker in talkers:
            ws_id = stable("ws", scoped(talker["id"]))
            if mapping(c, "talker:" + talker["id"], ws_id, talker):
                c.execute(
                    insert(workspaces).values(
                        id=ws_id,
                        owner_user_id=None,
                        name=f"旧空间 · {talker.get('name', talker['id'])}"[:160],
                        state="unclaimed_legacy",
                        legacy_id=scoped(talker["id"]),
                        created_at=now(),
                    )
                )
        for state in sessions:
            talker_id = state.get("talker_id") or "talker-001"
            ws_id, thread_id = (
                stable("ws", scoped(talker_id)),
                stable("thread", scoped(state["id"])),
            )
            if not c.execute(select(workspaces.c.id).where(workspaces.c.id == ws_id)).first():
                c.execute(
                    insert(workspaces).values(
                        id=ws_id,
                        owner_user_id=None,
                        name="旧空间 · " + talker_id[:100],
                        state="unclaimed_legacy",
                        legacy_id=scoped(talker_id),
                        created_at=now(),
                    )
                )
            if not mapping(c, "session:" + state["id"], thread_id, state):
                continue
            timeline = [
                (
                    m,
                    "user"
                    if m.get("type") in ("talker", "user", "message")
                    else "assistant"
                    if m.get("type") == "ai"
                    else "hinter",
                )
                for m in state.get("messages", [])
                if m.get("type") != "screening"
            ]
            timeline += [
                (a, "assistant")
                for a in state.get("answers", [])
                if a.get("published_at")
                or a.get("status")
                in ("sent", "published", "retracted", "withdrawn", "completed", "interrupted")
            ]
            seen_ids = {item.get("id") for item, _ in timeline if item in state.get("messages", [])}
            timeline = [
                (item, role)
                for item, role in timeline
                if role != "assistant"
                or item in state.get("messages", [])
                or item.get("id") not in seen_ids
            ]
            timeline.sort(key=lambda pair: str(pair[0].get("created_at", "")))
            c.execute(
                insert(threads).values(
                    id=thread_id,
                    workspace_id=ws_id,
                    title=str(state.get("title") or "历史会议")[:160],
                    profile="meeting_assistant",
                    next_seq=len(timeline) + 1,
                    created_at=now(),
                )
            )
            for seq, (item, role) in enumerate(timeline, 1):
                item_id = stable("record", scoped(state["id"] + ":" + str(item.get("id", seq))))
                withdrawn = item.get("status") in ("withdrawn", "retracted", "revoked") or item.get(
                    "withdrawn", False
                )
                c.execute(
                    insert(records).values(
                        id=item_id,
                        thread_id=thread_id,
                        seq=seq,
                        actor_user_id=None,
                        role=role,
                        kind="legacy_record",
                        text=str(item.get("text", "")),
                        visibility="admin_private" if role == "hinter" else "user_shared",
                        revision=int(item.get("revision", 1)),
                        withdrawn=withdrawn,
                        metadata={"legacy": item},
                        created_at=now(),
                    )
                )
        # Wiki paths retain their original scope and private/public classification.
        by_hash = {hashlib.sha256(t["id"].encode()).hexdigest()[:32]: t["id"] for t in talkers}
        for file in report["files"]:
            relative = Path(file["path"])
            if relative.suffix != ".md" or "wiki" not in relative.parts:
                continue
            parts = relative.parts
            owner = (
                by_hash.get(parts[2]) if len(parts) > 3 and parts[1] == "talkers" else "talker-001"
            )
            ws_id = stable("ws", scoped(owner or "unmapped-wiki"))
            if not c.execute(select(workspaces.c.id).where(workspaces.c.id == ws_id)).first():
                c.execute(
                    insert(workspaces).values(
                        id=ws_id,
                        owner_user_id=None,
                        name="旧 Wiki · 未认领",
                        state="unclaimed_legacy",
                        legacy_id=scoped(owner or "unmapped-wiki"),
                        created_at=now(),
                    )
                )
            doc_id = stable("memory", scoped(str(relative)))
            if mapping(c, "file:" + str(relative), doc_id, file):
                content = (root / relative).read_text(encoding="utf-8", errors="replace")
                private = any(p in ("private", "hinter-001") for p in parts)
                c.execute(
                    insert(memories).values(
                        id=doc_id,
                        workspace_id=ws_id,
                        name=str(relative)[-160:],
                        visibility="admin_private" if private else "user_shared",
                        text=content,
                        revision=1,
                        metadata={
                            "legacy_path": str(relative),
                            "sha256": file["sha256"],
                            "archived": any(
                                p.startswith(".") or p in ("history", "versions") for p in parts
                            ),
                        },
                        created_at=now(),
                    )
                )
    hydrate_legacy_context(root)
    import_legacy_audit(root, source_scope)
    hydrated = hydrate_legacy_files(root)
    return {
        **report,
        "imported": True,
        "source_scope": source_scope,
        "audio_artifacts_imported": hydrated,
    }


def hydrate_legacy_files(root):
    """Import referenced audio and current record revisions; preserve orphan files in backup."""
    import mimetypes
    import shutil

    from sqlalchemy import update

    from .db import artifacts, revisions
    from .voice import file_root

    root = Path(root).resolve()
    source_scope = source_identity(root)
    imported = 0
    with transaction() as c:
        legacy_threads = list(
            c.execute(
                select(threads.c.id)
                .join(workspaces, workspaces.c.id == threads.c.workspace_id)
                .where(workspaces.c.legacy_id.startswith(source_scope + ":"))
            ).scalars()
        )
        values = [
            dict(r)
            for r in c.execute(
                select(records).where(records.c.thread_id.in_(legacy_threads))
            ).mappings()
        ]
    for record in values:
        item = record["metadata"].get("legacy", {})
        references = set()

        def collect(value):
            if isinstance(value, dict):
                for name, child in value.items():
                    if name in ("audio", "tts_audio") and isinstance(child, dict):
                        for key in ("filename", "id"):
                            if child.get(key):
                                references.add(Path(str(child[key])).name)
                    elif isinstance(child, (dict, list)):
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(item)
        if item.get("id"):
            references.add(Path(str(item["id"])).name)
        linked = []
        for reference in references:
            candidates = (
                [root / "audio" / reference]
                if Path(reference).suffix
                else list((root / "audio").glob(reference + ".*"))
            )
            for source in candidates:
                if not source.is_file() or source.is_symlink() or source.parent != root / "audio":
                    continue
                relative = f"{record['thread_id']}/{source_scope}-{source.name}"
                artifact_id = stable("artifact", relative)
                linked.append(artifact_id)
                with transaction() as c:
                    if c.execute(
                        select(artifacts.c.id).where(artifacts.c.id == artifact_id)
                    ).first():
                        continue
                destination = file_root() / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
                checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
                with transaction() as c:
                    c.execute(
                        insert(artifacts).values(
                            id=artifact_id,
                            thread_id=record["thread_id"],
                            path=relative,
                            media_type=mimetypes.guess_type(source.name)[0]
                            or "application/octet-stream",
                            checksum=checksum,
                            size=destination.stat().st_size,
                            created_at=now(),
                        )
                    )
                imported += 1
        with transaction() as c:
            if not c.execute(
                select(revisions.c.record_id).where(
                    revisions.c.record_id == record["id"],
                    revisions.c.revision == record["revision"],
                )
            ).first():
                c.execute(
                    insert(revisions).values(
                        record_id=record["id"],
                        revision=record["revision"],
                        text=record["text"],
                        actor_user_id=None,
                        created_at=record["created_at"],
                    )
                )
            c.execute(
                update(records)
                .where(records.c.id == record["id"])
                .values(
                    metadata={
                        **record["metadata"],
                        "artifact_ids": sorted(
                            set(linked) | set(record["metadata"].get("artifact_ids", []))
                        ),
                    }
                )
            )
    return imported


def import_legacy_audit(root, scope):
    """Keep the complete legacy settings/events/hint revisions as sealed migration data."""
    from .db import uid

    with sqlite3.connect(f"file:{Path(root) / 'duet.db'}?mode=ro", uri=True) as source:
        existing = {
            r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in (
            "settings",
            "events",
            "commands",
            "guidance_events",
            "deleted_talkers",
            "deleted_sessions",
        ):
            if table not in existing:
                continue
            cursor = source.execute(f"SELECT * FROM {table}")
            names = [d[0] for d in cursor.description]
            for index, values in enumerate(cursor):
                payload = dict(zip(names, values))
                key = scope + ":" + hashlib.sha256(f"audit:{table}:{index}".encode()).hexdigest()
                checksum = hashlib.sha256(
                    json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                with transaction() as c:
                    old = (
                        c.execute(select(legacy_maps).where(legacy_maps.c.source_key == key))
                        .mappings()
                        .first()
                    )
                    if old:
                        if old["checksum"] != checksum:
                            raise ValueError("Legacy audit source changed")
                    else:
                        c.execute(
                            insert(legacy_maps).values(
                                source_key=key,
                                target_id=uid("legacy_audit"),
                                checksum=checksum,
                                payload={"source_table": table, "data": payload},
                                created_at=now(),
                            )
                        )


def hydrate_legacy_context(root):
    from datetime import datetime

    from sqlalchemy import update

    from .db import projections

    root = Path(root).resolve()
    scope = source_identity(root)
    fallback = (root / "duet.db").stat().st_mtime

    def timestamp(value):
        if isinstance(value, (int, float)):
            return float(value)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()
        except (ValueError, TypeError):
            return fallback

    with sqlite3.connect(f"file:{root / 'duet.db'}?mode=ro", uri=True) as source:
        states = [json.loads(row[0]) for row in source.execute("SELECT state FROM sessions")]
    with transaction() as c:
        for state in states:
            thread_id = stable("thread", scope + ":" + state["id"])
            c.execute(
                update(threads)
                .where(threads.c.id == thread_id)
                .values(created_at=timestamp(state.get("created_at")))
            )
            items = {}
            for message in [*state.get("messages", []), *state.get("answers", [])]:
                if not message.get("id"):
                    continue
                record_id = stable("record", scope + ":" + state["id"] + ":" + message["id"])
                c.execute(
                    update(records)
                    .where(records.c.id == record_id)
                    .values(created_at=timestamp(message.get("created_at")))
                )
            for question in state.get("questions", []):
                for source_id in question.get("source_message_ids", []):
                    record_id = stable("record", scope + ":" + state["id"] + ":" + source_id)
                    record = (
                        c.execute(select(records).where(records.c.id == record_id))
                        .mappings()
                        .first()
                    )
                    if record:
                        items.setdefault(record_id, []).append(
                            {
                                "text": question.get("text", ""),
                                "source_record_id": record_id,
                                "source_revision": record["revision"],
                                "legacy_question_id": question.get("id"),
                                "legacy_status": question.get("status"),
                                "related_run_id": None,
                            }
                        )
            for record_id, questions in items.items():
                if not c.execute(
                    select(projections.c.record_id).where(
                        projections.c.record_id == record_id, projections.c.kind == "questions"
                    )
                ).first():
                    c.execute(
                        insert(projections).values(
                            record_id=record_id,
                            kind="questions",
                            source_revision=questions[0]["source_revision"],
                            items=questions,
                            status="ready",
                        )
                    )
