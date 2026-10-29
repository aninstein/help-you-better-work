"""Persist every human Hinter hint as an owner-scoped, revisioned MySQL Wiki entry."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, insert, or_, select, update

from .db import memories, records, threads, transaction


def public_source_guard():
    origin = func.coalesce(
        func.JSON_UNQUOTE(func.JSON_EXTRACT(memories.c.metadata, "$.origin")), ""
    )
    source_id = func.JSON_UNQUOTE(func.JSON_EXTRACT(memories.c.metadata, "$.source_record_id"))
    return or_(
        origin != "hinter",
        exists(
            select(records.c.id).where(
                records.c.id == source_id,
                records.c.visibility == "user_shared",
                records.c.withdrawn.is_(False),
            )
        ),
    )


def sync_hint(c, record):
    if record["role"] != "hinter" or record["kind"] not in ("guidance", "legacy_record"):
        return None
    from .answer_policy import sync_from_hint

    sync_from_hint(c, record)
    if not record["text"].strip():
        return None
    # A system-generated refusal contains no human-supplied knowledge.
    if record["metadata"].get("resolution") == "declined":
        return None
    thread = c.execute(select(threads).where(threads.c.id == record["thread_id"])).mappings().one()
    source = None
    if record["source_record_id"]:
        source = (
            c.execute(
                select(records).where(
                    records.c.id == record["source_record_id"],
                    records.c.thread_id == record["thread_id"],
                )
            )
            .mappings()
            .first()
        )
    # Never turn a reference to private material into a shared Wiki article.
    context = source["text"] if source and source["visibility"] == "user_shared" else ""
    title = (context or thread["title"] or "Hinter 补充").replace("\n", " ")[:60]
    stamp = datetime.fromtimestamp(record["created_at"], ZoneInfo("Asia/Shanghai"))
    name = f"Hinter · {title} · {stamp:%m-%d %H:%M:%S.%f}"
    content = (f"原问题：{context}\n\n" if context else "") + record["text"]
    memory_id = "memory_hint_" + record["id"].removeprefix("record_")
    old = (
        c.execute(select(memories).where(memories.c.id == memory_id).with_for_update())
        .mappings()
        .first()
    )
    metadata = {
        "origin": "hinter",
        "memory_layer": "long_term",
        "scope": "talker" if record["visibility"] == "user_shared" else "admin_private",
        "principle": bool(
            re.match(
                r"^(?:请|以后)?(?:回答|回复|始终|必须|应该|禁止|不要|不准|不允许|只能|优先|尽量)",
                record["text"].strip(),
            )
        ),
        "source_record_id": record["id"],
        "source_revision": record["revision"],
        "thread_id": record["thread_id"],
        "title": title,
        "archived": bool(record["withdrawn"]),
    }
    if old:
        if (
            old["metadata"].get("source_revision") == record["revision"]
            and old["visibility"] == record["visibility"]
            and old["text"] == content
            and bool(old["metadata"].get("archived")) == bool(record["withdrawn"])
            and old["metadata"].get("scope") == metadata["scope"]
            and old["metadata"].get("memory_layer") == metadata["memory_layer"]
        ):
            return old["id"]
        history = [
            *old["metadata"].get("revisions", []),
            {
                "revision": old["revision"],
                "text": old["text"],
                "actor_user_id": record["actor_user_id"],
            },
        ]
        c.execute(
            update(memories)
            .where(memories.c.id == memory_id)
            .values(
                text=content,
                visibility=record["visibility"],
                revision=old["revision"] + 1,
                metadata={**metadata, "revisions": history},
            )
        )
    else:
        c.execute(
            insert(memories).values(
                id=memory_id,
                workspace_id=thread["workspace_id"],
                name=name,
                text=content,
                visibility=record["visibility"],
                revision=1,
                metadata=metadata,
                created_at=record["created_at"],
            )
        )
    return memory_id


def backfill():
    after, count = "", 0
    while True:
        with transaction() as c:
            ids = list(
                c.execute(
                    select(records.c.id)
                    .where(
                        records.c.role == "hinter",
                        records.c.kind.in_(("guidance", "legacy_record")),
                        records.c.id > after,
                    )
                    .order_by(records.c.id)
                    .limit(100)
                ).scalars()
            )
        if not ids:
            return count
        for record_id in ids:
            with transaction() as c:
                record = (
                    c.execute(select(records).where(records.c.id == record_id).with_for_update())
                    .mappings()
                    .one()
                )
                if sync_hint(c, record):
                    count += 1
        after = ids[-1]


if __name__ == "__main__":
    print(f"Hinter Wiki synchronized: {backfill()} entries")
