"""Transactional record/run contracts; every resource is authorized at its owner boundary."""

import hashlib
import json
from contextlib import nullcontext

from sqlalchemy import insert, select, update

from .auth import audit_event, fail, require
from .db import (
    audit,
    commands,
    events,
    memories,
    now,
    projections,
    records,
    revisions,
    runs,
    threads,
    transaction,
    uid,
    users,
    workspaces,
)

TERMINAL = {"completed", "failed", "cancelled"}


def workspace_access(c, actor, workspace_id, *, write=False):
    if write:
        fresh_actor = (
            c.execute(select(users).where(users.c.id == actor["id"]).with_for_update())
            .mappings()
            .one()
        )
        if (
            fresh_actor["auth_version"] != actor["auth_version"]
            or fresh_actor["status"] != "active"
        ):
            fail(403, "PERMISSION_DENIED", "账号权限已变化，请重新登录。")
    ws = c.execute(select(workspaces).where(workspaces.c.id == workspace_id)).mappings().first()
    if not ws or (ws["owner_user_id"] != actor["id"] and "hinter_admin" not in actor["roles"]):
        fail(404, "NOT_FOUND", "工作空间不存在。")
    if ws["state"] == "unclaimed_legacy" and "hinter_admin" not in actor["roles"]:
        fail(404, "NOT_FOUND", "工作空间不存在。")
    if write and not ws["owner_user_id"]:
        fail(409, "LEGACY_UNCLAIMED", "请先将旧空间绑定到注册用户。")
    return dict(ws)


def thread_access(c, actor, thread_id, *, write=False, lock=False):
    statement = select(threads).where(threads.c.id == thread_id)
    if lock:
        statement = statement.with_for_update()
    t = c.execute(statement).mappings().first()
    if not t:
        fail(404, "NOT_FOUND", "会话不存在。")
    ws = workspace_access(c, actor, t["workspace_id"], write=write)
    return dict(t), ws


def create_thread(actor, workspace_id, title="新会议", profile="meeting_assistant"):
    if profile != "meeting_assistant":
        fail(422, "PROFILE_UNSUPPORTED", "当前仅支持会议助手配置。")
    with transaction() as c:
        workspace_access(c, actor, workspace_id, write=True)
        t = {
            "id": uid("thread"),
            "workspace_id": workspace_id,
            "title": title.strip()[:160] or "新会议",
            "profile": profile,
            "next_seq": 1,
            "created_at": now(),
        }
        c.execute(insert(threads).values(**t))
        return t


def append_record(
    c,
    thread,
    *,
    actor_id,
    role,
    text,
    kind="text",
    visibility="user_shared",
    source_id=None,
    record_id=None,
    metadata=None,
):
    record = {
        "id": record_id or uid("record"),
        "thread_id": thread["id"],
        "seq": thread["next_seq"],
        "actor_user_id": actor_id,
        "role": role,
        "kind": kind,
        "text": text,
        "visibility": visibility,
        "revision": 1,
        "withdrawn": False,
        "source_record_id": source_id,
        "metadata": metadata or {},
        "created_at": now(),
    }
    c.execute(insert(records).values(**record))
    c.execute(
        insert(revisions).values(
            record_id=record["id"], revision=1, text=text, actor_user_id=actor_id, created_at=now()
        )
    )
    thread["next_seq"] += 1
    c.execute(
        update(threads).where(threads.c.id == thread["id"]).values(next_seq=thread["next_seq"])
    )
    if role == "hinter" and kind == "guidance":
        from .wiki import sync_hint

        sync_hint(c, record)
    return record


def event(c, run, kind, payload=None):
    locked = c.execute(
        select(runs.c.next_event_seq).where(runs.c.id == run["id"]).with_for_update()
    ).scalar_one()
    item = {
        "id": uid("event"),
        "run_id": run["id"],
        "thread_id": run["thread_id"],
        "seq": locked,
        "type": kind,
        "payload": payload or {},
        "source_record_ids": [run["record_id"]],
        "created_at": now(),
    }
    c.execute(insert(events).values(**item))
    c.execute(
        update(runs)
        .where(runs.c.id == run["id"])
        .values(next_event_seq=locked + 1, updated_at=now())
    )
    return item


def queue_run(
    c,
    actor,
    thread_id,
    owner_id,
    record_id,
    *,
    kind="interactive",
    parent_run_id=None,
    source_revision=1,
    immediate_reply=None,
):
    if kind == "interactive" and immediate_reply is None:
        heartbeat = c.execute(
            select(audit.c.created_at)
            .where(audit.c.action == "worker.ready")
            .order_by(audit.c.created_at.desc())
            .limit(1)
        ).scalar()
        if not heartbeat or heartbeat < now() - 45:
            fail(
                503, "AGENT_RUNTIME_UNAVAILABLE", "Agent 服务尚未就绪，请稍后重试；输入内容已保留。"
            )
    run = {
        "id": uid("run"),
        "kind": kind,
        "parent_run_id": parent_run_id,
        "source_revision": source_revision,
        "thread_id": thread_id,
        "record_id": record_id,
        "actor_user_id": actor["id"],
        "owner_user_id": owner_id,
        "auth_version": actor["auth_version"],
        "status": "queued",
        "attempt": 0,
        "cancel_requested": False,
        "next_event_seq": 1,
        "created_at": now(),
        "updated_at": now(),
    }
    c.execute(insert(runs).values(**run))
    event(c, run, "run.accepted" if immediate_reply is not None else "run.queued")
    if immediate_reply is not None:
        thread = dict(
            c.execute(select(threads).where(threads.c.id == thread_id).with_for_update())
            .mappings()
            .one()
        )
        run["assistant_record"] = publish_policy_refusal(c, thread, run, immediate_reply)
        run["status"] = "completed"
    return run


def publish_policy_refusal(c, thread, run, text):
    """Normal, durable assistant output without any model, tool or worker dependency."""
    reply = append_record(
        c,
        thread,
        actor_id=run["actor_user_id"],
        role="assistant",
        kind="agent_output",
        text=text,
        source_id=run["record_id"],
        metadata={"run_id": run["id"], "runtime": "policy-gate", "policy_blocked": True},
    )
    c.execute(
        update(runs)
        .where(runs.c.id == run["id"])
        .values(
            status="completed",
            error_code=None,
            lease_until=None,
            cancel_requested=False,
            updated_at=now(),
        )
    )
    event(c, run, "assistant.completed", {"text": text})
    event(c, run, "run.completed", {"policy_blocked": True})
    return reply


def recover_policy_refusal(run_id):
    """Repair an existing failed request only when its current policy requires refusal."""
    from .answer_policy import match_input, refusal
    from .input_preprocessing import detect_language

    with transaction() as c:
        old = c.execute(select(runs).where(runs.c.id == run_id)).mappings().one()
        thread = dict(
            c.execute(select(threads).where(threads.c.id == old["thread_id"]).with_for_update())
            .mappings()
            .one()
        )
        run = dict(
            c.execute(select(runs).where(runs.c.id == run_id).with_for_update()).mappings().one()
        )
        if run["kind"] != "interactive" or run["status"] != "failed":
            return None
        source = c.execute(select(records).where(records.c.id == run["record_id"])).mappings().one()
        if source["visibility"] != "user_shared" or source["withdrawn"]:
            return None
        rule = match_input(c, run["thread_id"], source["text"])
        if not rule:
            return None
        language = source["metadata"].get("input_preprocessing", {}).get(
            "reply_language"
        ) or detect_language(source["text"])
        reply = publish_policy_refusal(c, thread, run, refusal(language))
        c.execute(
            update(projections)
            .where(projections.c.record_id == source["id"])
            .values(status="ready", items=[])
        )
        c.execute(
            update(records)
            .where(records.c.id == source["id"])
            .values(
                metadata={
                    **source["metadata"],
                    "policy_blocked": True,
                    "answer_policy_id": rule["id"],
                }
            )
        )
        audit_event(c, None, "policy.refusal_recovered", run_id, {"source_record_id": source["id"]})
        return reply


def submit_input(actor, thread_id, body, key):
    if body.presentation_mode != "questions":
        fail(422, "PRESENTATION_MODE_UNSUPPORTED", "当前支持 questions；摘要视图尚未开放。")
    if body.agent_id != "personal-employee" or body.profile != "meeting_assistant":
        fail(422, "AGENT_UNSUPPORTED", "Agent 或工作配置无效。")
    if not key or len(key) > 100:
        fail(422, "IDEMPOTENCY_KEY_REQUIRED", "请求需要有效 Idempotency-Key。")
    request_hash = hashlib.sha256(
        json.dumps(body.model_dump(), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    # Authorize and check replay before making a bounded model request. Never hold
    # a thread lock or database connection while preprocessing waits on a provider.
    from .input_preprocessing import detect_language, preprocess

    with transaction() as c:
        _, ws = thread_access(c, actor, thread_id, write=True)
        previous = (
            c.execute(
                select(commands).where(
                    commands.c.owner_user_id == ws["owner_user_id"],
                    commands.c.operation == f"input:{thread_id}",
                    commands.c.key == key,
                )
            )
            .mappings()
            .first()
        )
        if previous:
            if previous["request_hash"] != request_hash:
                fail(409, "IDEMPOTENCY_CONFLICT", "同一请求标识对应不同内容。")
            return previous["response"]
        context = [
            {
                "role": row["role"],
                "text": row["text"][-500:],
                "reply_language": row["metadata"]
                .get("input_preprocessing", {})
                .get("reply_language", detect_language(row["text"])),
            }
            for row in c.execute(
                select(records)
                .where(
                    records.c.thread_id == thread_id,
                    records.c.role.in_(("user", "assistant")),
                    records.c.visibility == "user_shared",
                    records.c.withdrawn.is_(False),
                )
                .order_by(records.c.seq.desc())
                .limit(4)
            ).mappings()
        ][::-1]
    decision = preprocess(body.input.text, body.input.kind, context)
    with transaction() as c:
        thread, ws = thread_access(c, actor, thread_id, write=True, lock=True)
        operation = f"input:{thread_id}"
        previous = (
            c.execute(
                select(commands).where(
                    commands.c.owner_user_id == ws["owner_user_id"],
                    commands.c.operation == operation,
                    commands.c.key == key,
                )
            )
            .mappings()
            .first()
        )
        if previous:
            if previous["request_hash"] != request_hash:
                fail(409, "IDEMPOTENCY_CONFLICT", "同一请求标识对应不同内容。")
            return previous["response"]
        source_id = body.input.source_record_id
        if source_id:
            source = (
                c.execute(
                    select(records)
                    .where(records.c.id == source_id, records.c.thread_id == thread_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if not source or source["role"] != "user":
                fail(404, "NOT_FOUND", "来源记录不存在。")
        if not decision.accepted:
            result = {
                "ignored": True,
                "reason": decision.reason,
                "retryable": decision.retryable,
                "message": "这段语音暂时无法确认，请重试或改用文字。" if decision.retryable else "",
                "status": "ignored",
                "thread_id": thread_id,
                "record_id": None,
                "run_id": None,
            }
            # A temporary classifier outage can be retried with the same key.
            if decision.retryable:
                return result
            c.execute(
                insert(commands).values(
                    owner_user_id=ws["owner_user_id"],
                    operation=operation,
                    key=key,
                    request_hash=request_hash,
                    response=result,
                )
            )
            return result
        from .answer_policy import match_input, refusal

        matched_policy = match_input(c, thread_id, body.input.text)
        policy_blocked = bool(matched_policy)
        record = append_record(
            c,
            thread,
            actor_id=actor["id"],
            role="user",
            text=body.input.text,
            kind=body.input.kind,
            source_id=source_id,
            metadata={
                "input_preprocessing": {**decision.metadata(), "source_revision": 1},
                "policy_blocked": policy_blocked,
                "answer_policy_id": matched_policy["id"] if matched_policy else None,
            },
        )
        run = queue_run(
            c,
            actor,
            thread_id,
            ws["owner_user_id"],
            record["id"],
            immediate_reply=refusal(decision.reply_language) if policy_blocked else None,
        )
        # Projection generation is a separate DeerFlow skill after main output.
        c.execute(
            insert(projections).values(
                record_id=record["id"],
                kind="questions",
                source_revision=1,
                items=[],
                status="ready" if policy_blocked else "pending",
            )
        )
        from .voice_cues import cue_for

        result = {
            "acknowledgement": None
            if policy_blocked
            else cue_for(body.input.text, decision.reply_language),
            "reply_language": decision.reply_language,
            "record_id": record["id"],
            "thread_id": thread_id,
            "run_id": run["id"],
            "status": run["status"],
            "assistant_record": run.get("assistant_record"),
            "input_record": record if policy_blocked else None,
            "presentation_mode": "questions",
        }
        c.execute(
            insert(commands).values(
                owner_user_id=ws["owner_user_id"],
                operation=operation,
                key=key,
                request_hash=request_hash,
                response=result,
            )
        )
        if thread["title"] == "新会议":
            c.execute(
                update(threads).where(threads.c.id == thread_id).values(title=body.input.text[:40])
            )
        return result


def visible_records(c, actor, thread_id, after_seq=0, limit=200):
    thread_access(c, actor, thread_id)
    query = select(records).where(records.c.thread_id == thread_id, records.c.seq > after_seq)
    if "hinter_admin" not in actor["roles"]:
        query = query.where(records.c.visibility == "user_shared")
    values = []
    for r in c.execute(query.order_by(records.c.seq).limit(limit)).mappings():
        item = dict(r)
        if item["role"] == "assistant":
            from .answer_text import public_answer

            item["text"] = public_answer(item["text"])
        if item["withdrawn"]:
            item["text"], item["metadata"] = "已撤回 · 内容已隐藏", {}
        values.append(item)
    return values


def run_access(c, actor, run_id):
    run = c.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
    if not run:
        fail(404, "NOT_FOUND", "任务不存在。")
    thread_access(c, actor, run["thread_id"])
    return dict(run)


def put_memory(
    actor, workspace_id, name, text, visibility, expected_revision=None, *, connection=None
):
    if visibility not in ("user_shared", "admin_private"):
        fail(422, "VALIDATION_ERROR", "可见范围无效。")
    with nullcontext(connection) if connection is not None else transaction() as c:
        ws = workspace_access(c, actor, workspace_id, write=True)
        if ws["owner_user_id"] != actor["id"] or visibility == "admin_private":
            require(actor, "guidance.write")
        c.execute(
            select(workspaces.c.id).where(workspaces.c.id == workspace_id).with_for_update()
        ).one()
        old = (
            c.execute(
                select(memories)
                .where(memories.c.workspace_id == workspace_id, memories.c.name == name)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if old and old["visibility"] == "admin_private":
            require(actor, "guidance.write")
        if old and old["metadata"].get("origin") == "answer_policy":
            fail(409, "POLICY_MANAGED", "请在回答限制面板管理该规则。")
        if old and old["revision"] != expected_revision:
            fail(409, "VERSION_CONFLICT", "记忆已变化，请重新加载后保存。")
        if old:
            history = list(old["metadata"].get("revisions", []))
            history.append(
                {
                    "revision": old["revision"],
                    "text": old["text"],
                    "actor_user_id": actor["id"],
                    "at": now(),
                }
            )
            c.execute(
                update(memories)
                .where(memories.c.id == old["id"])
                .values(
                    text=text,
                    visibility=visibility,
                    revision=old["revision"] + 1,
                    metadata={**old["metadata"], "revisions": history},
                )
            )
            target, revision = old["id"], old["revision"] + 1
        else:
            target, revision = uid("memory"), 1
            c.execute(
                insert(memories).values(
                    id=target,
                    workspace_id=workspace_id,
                    name=name,
                    visibility=visibility,
                    text=text,
                    revision=1,
                    metadata={},
                    created_at=now(),
                )
            )
        audit_event(
            c, actor["id"], "memory.saved", target, {"revision": revision, "visibility": visibility}
        )
        return {"id": target, "revision": revision}
