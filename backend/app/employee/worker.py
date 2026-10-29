"""Lease-fenced MySQL queue worker running the official DeerFlow lead agent."""

import json
import logging
import multiprocessing
import os
import signal
import threading
import time

from sqlalchemy import delete, insert, or_, select, update

from . import names
from . import repository as repo
from .agent_tools import ACTIVE_RUN, current_grant
from .answer_policy import AnswerRestricted, ensure_run_allowed, refusal
from .answer_text import public_answer
from .db import audit, now, projections, records, runs, threads, transaction, uid, users
from .question_projection import deduplicate_questions
from .runtime import make_client

log = logging.getLogger(__name__)
STOP = threading.Event()
WORK_KIND = os.getenv("AGENT_WORKER_KIND", "interactive")
if WORK_KIND not in ("interactive", "projection"):
    raise RuntimeError("AGENT_WORKER_KIND must be interactive or projection")
LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


class InterruptedRun(Exception):
    pass


def claim():
    with transaction() as c:
        if WORK_KIND == "projection":
            run = (
                c.execute(
                    select(runs)
                    .where(
                        runs.c.kind == "projection",
                        runs.c.status.in_(["queued", "running"]),
                        or_(runs.c.lease_until.is_(None), runs.c.lease_until < now()),
                    )
                    .order_by(runs.c.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
        else:
            eligible = select(runs.c.thread_id).where(
                runs.c.kind == "interactive", runs.c.status.in_(["queued", "running"])
            )
            thread = (
                c.execute(
                    select(threads)
                    .where(
                        threads.c.id.in_(eligible),
                        or_(threads.c.lease_until.is_(None), threads.c.lease_until < now()),
                    )
                    .order_by(threads.c.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not thread:
                return None
            run = (
                c.execute(
                    select(runs)
                    .join(records, records.c.id == runs.c.record_id)
                    .where(
                        runs.c.thread_id == thread["id"],
                        runs.c.kind == "interactive",
                        runs.c.status.in_(["queued", "running"]),
                    )
                    .order_by(records.c.seq, runs.c.created_at, runs.c.id)
                    .with_for_update()
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if not run:
            return None
        value = {
            **dict(run),
            "status": "running",
            "attempt": run["attempt"] + 1,
            "lease_token": uid("lease"),
            "lease_until": now() + LEASE_SECONDS,
            "updated_at": now(),
        }
        c.execute(
            update(runs)
            .where(runs.c.id == value["id"])
            .values(
                **{
                    k: value[k]
                    for k in ("status", "attempt", "lease_token", "lease_until", "updated_at")
                }
            )
        )
        if WORK_KIND == "interactive":
            c.execute(
                update(threads)
                .where(threads.c.id == value["thread_id"])
                .values(lease_run_id=value["id"], lease_until=value["lease_until"])
            )
        repo.event(
            c,
            value,
            "run.started",
            {"attempt": value["attempt"], "runtime": "deerflow-2.1.0", "kind": WORK_KIND},
        )
        return value


def context_for(run, purpose="meeting"):
    return {
        "user_id": run["owner_user_id"],
        "user_role": "user",
        "authz_attributes": {
            "app_run_id": run["id"],
            "lease_token": run["lease_token"],
            "purpose": purpose,
            "actor_user_id": run["actor_user_id"],
            "owner_user_id": run["owner_user_id"],
            "auth_version": run["auth_version"],
        },
    }


def verify(c, run):
    current = (
        c.execute(select(runs).where(runs.c.id == run["id"]).with_for_update()).mappings().one()
    )
    if (
        current["status"] != "running"
        or current["lease_token"] != run["lease_token"]
        or current["lease_until"] < now()
    ):
        raise InterruptedRun("LEASE_EXPIRED")
    actor = c.execute(select(users).where(users.c.id == run["actor_user_id"])).mappings().one()
    if current["cancel_requested"]:
        raise InterruptedRun("CANCELLED")
    if (
        actor["status"] != "active"
        or actor["auth_version"] != run["auth_version"]
        or actor["id"] != run["owner_user_id"]
        and "hinter_admin" not in actor["roles"]
    ):
        raise InterruptedRun("AUTHORIZATION_REVOKED")
    return current


def heartbeat(run, finished):
    while not finished.wait(5):
        try:
            with transaction() as c:
                if run["kind"] == "interactive":
                    c.execute(
                        select(threads.c.id)
                        .where(threads.c.id == run["thread_id"])
                        .with_for_update()
                    ).one()
                verify(c, run)
                until = now() + LEASE_SECONDS
                c.execute(update(runs).where(runs.c.id == run["id"]).values(lease_until=until))
                if run["kind"] == "interactive":
                    c.execute(
                        update(threads)
                        .where(
                            threads.c.id == run["thread_id"], threads.c.lease_run_id == run["id"]
                        )
                        .values(lease_until=until)
                    )
                repo_ready(c)
        except Exception:
            # The main stream rechecks authorization/fencing before publication.
            return


def repo_ready(c):
    action = "worker.ready" if WORK_KIND == "interactive" else "projection.worker.ready"
    # Do not range-delete both workers' heartbeat index immediately after an
    # insert: concurrent startup can deadlock on each other's next-key locks.
    # Select stale IDs without locks, then delete only existing primary keys.
    stale = list(
        c.execute(
            select(audit.c.id)
            .where(audit.c.action == action, audit.c.created_at < now() - 3600)
            .order_by(audit.c.id)
            .limit(200)
        ).scalars()
    )
    if stale:
        c.execute(delete(audit).where(audit.c.id.in_(stale)))
    c.execute(
        insert(audit).values(
            id=uid("health"),
            actor_user_id=None,
            action=action,
            target_id=os.getenv("HOSTNAME", "local"),
            payload={"runtime": "deerflow-2.1.0", "persistence": "mysql"},
            created_at=now(),
        )
    )


def emit(run, kind, payload, *, policy_refusal=False):
    with transaction() as c:
        verify(c, run)
        if kind.startswith("assistant.") and not policy_refusal:
            ensure_run_allowed(c, run, payload.get("text", ""))
        repo.event(c, run, kind, payload)


def complete(run, status, text="", error=None, *, policy_blocked=False):
    with transaction() as c:
        thread = dict(
            c.execute(select(threads).where(threads.c.id == run["thread_id"]).with_for_update())
            .mappings()
            .one()
        )
        value = (
            c.execute(select(runs).where(runs.c.id == run["id"]).with_for_update()).mappings().one()
        )
        if (
            value["lease_token"] != run["lease_token"]
            or value["status"] != "running"
            or value["lease_until"] < now()
        ):
            return
        if status == "completed":
            verify(c, run)
            if run["kind"] == "interactive" and not policy_blocked:
                try:
                    ensure_run_allowed(c, run, text)
                except AnswerRestricted as denied:
                    text = refusal(denied.language)
                    policy_blocked = True
                    repo.event(c, run, "assistant.completed", {"text": text})
        if status == "completed" and run["kind"] == "interactive":
            repo.append_record(
                c,
                thread,
                actor_id=run["actor_user_id"],
                role="assistant",
                kind="agent_output",
                text=text,
                metadata={
                    "run_id": run["id"],
                    "runtime": "deerflow-2.1.0",
                    "policy_blocked": policy_blocked,
                },
            )
        if status == "completed" and run["kind"] == "interactive" and not policy_blocked:
            actor = dict(
                c.execute(select(users).where(users.c.id == run["actor_user_id"])).mappings().one()
            )
            repo.queue_run(
                c,
                actor,
                run["thread_id"],
                run["owner_user_id"],
                run["record_id"],
                kind="projection",
                parent_run_id=run["id"],
                source_revision=run["source_revision"],
            )
        elif policy_blocked:
            c.execute(
                update(projections)
                .where(
                    projections.c.record_id == run["record_id"],
                    projections.c.source_revision == run["source_revision"],
                )
                .values(items=[], status="ready")
            )
        elif status != "completed" and run["kind"] == "projection":
            c.execute(
                update(projections)
                .where(
                    projections.c.record_id == run["record_id"],
                    projections.c.source_revision == run["source_revision"],
                )
                .values(status="failed")
            )
        c.execute(
            update(runs)
            .where(runs.c.id == run["id"])
            .values(status=status, error_code=error, updated_at=now(), lease_until=None)
        )
        c.execute(
            update(threads)
            .where(threads.c.id == run["thread_id"], threads.c.lease_run_id == run["id"])
            .values(lease_run_id=None, lease_until=None)
        )
        repo.event(c, run, f"run.{status}", {"error_code": error} if error else {})


def projection(client, run, source):
    try:
        from .runtime import ROOT

        instruction = (ROOT / "runtime/skills/public/question-projection/SKILL.md").read_text()
        prompt = (
            instruction
            + "\n这是只读展示投影，请只输出 JSON，不回答源问题。\nSOURCE_JSON:\n"
            + json.dumps(source["text"], ensure_ascii=False)
        )
        client.reset_agent()
        text = client.chat(
            prompt,
            thread_id=f"projection-{source['id']}-v{source['revision']}",
            **context_for(run, purpose="projection"),
            model_name="projection",
            subagent_enabled=False,
            recursion_limit=40,
        )
        clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(clean)
        items = []
        for item in data["items"]:
            # Ground the projection in an actual source span, never a model-invented question.
            question = str(item["text"])
            start = source["text"].find(question)
            if start >= 0:
                items.append(
                    {
                        "text": question,
                        "start": start,
                        "end": start + len(question),
                        "source_record_id": source["id"],
                        "source_revision": source["revision"],
                        "related_run_id": run.get("parent_run_id") or run["id"],
                    }
                )
        items = deduplicate_questions(items)
        with transaction() as c:
            verify(c, run)
            c.execute(
                update(projections)
                .where(
                    projections.c.record_id == source["id"],
                    projections.c.source_revision == source["revision"],
                )
                .values(items=items, status="ready")
            )
            repo.event(c, run, "projection.updated", {"record_id": source["id"], "items": items})
        return True
    except Exception as exc:
        log.warning("Projection %s failed: %s", source["id"], type(exc).__name__)
        with transaction() as c:
            c.execute(
                update(projections)
                .where(projections.c.record_id == source["id"])
                .values(status="failed")
            )
        return False
    finally:
        client.reset_agent()


def execute(client, run):
    bound_run = ACTIVE_RUN.set(run)
    client._checkpointer.bind_run(run)
    finished = threading.Event()
    keeper = threading.Thread(target=heartbeat, args=(run, finished), daemon=True)
    keeper.start()
    try:
        current_grant(context_for(run))
        if run["attempt"] > MAX_ATTEMPTS:
            complete(run, "failed", error="RECOVERY_LIMIT_EXCEEDED")
            return
        with transaction() as c:
            source = dict(
                c.execute(select(records).where(records.c.id == run["record_id"])).mappings().one()
            )
            if source["withdrawn"]:
                raise InterruptedRun("SOURCE_WITHDRAWN")
            if source["visibility"] != "user_shared":
                raise InterruptedRun("PRIVATE_SOURCE")
            original_question = None
            if source["metadata"].get("assistance_request_id"):
                original_question = (
                    c.execute(
                        select(records).where(
                            records.c.id == source["source_record_id"],
                            records.c.thread_id == run["thread_id"],
                            records.c.visibility == "user_shared",
                            records.c.withdrawn.is_(False),
                        )
                    )
                    .mappings()
                    .first()
                )
                if not original_question or original_question["revision"] != source["metadata"].get(
                    "original_revision"
                ):
                    raise InterruptedRun("ASSISTANCE_SOURCE_CHANGED")
            if run["kind"] == "projection" and source["revision"] != run["source_revision"]:
                raise InterruptedRun("SOURCE_REVISION_SUPERSEDED")
            if source["revision"] != run["source_revision"]:
                from .db import revisions

                original = (
                    c.execute(
                        select(revisions).where(
                            revisions.c.record_id == source["id"],
                            revisions.c.revision == run["source_revision"],
                        )
                    )
                    .mappings()
                    .one()
                )
                source.update(text=original["text"], revision=run["source_revision"])
            # Only user-visible context enters the model; private notes never do.
            history = [
                dict(r)
                for r in c.execute(
                    select(records)
                    .where(
                        records.c.thread_id == run["thread_id"],
                        records.c.seq <= source["seq"],
                        records.c.visibility == "user_shared",
                        records.c.withdrawn.is_(False),
                    )
                    .order_by(records.c.seq.desc())
                    .limit(30)
                ).mappings()
            ]
        if run["kind"] == "projection":
            success = projection(client, run, source)
            complete(
                run,
                "completed" if success else "failed",
                error=None if success else "PROJECTION_FAILED",
            )
            return
        history = [source if r["id"] == source["id"] else r for r in history]
        history.reverse()
        from .memory_context import assemble

        message = assemble(run["owner_user_id"], history, source)
        from .input_preprocessing import language_instruction, preprocess

        language_source = original_question or source
        preprocessing = language_source["metadata"].get("input_preprocessing", {})
        language = preprocessing.get("reply_language")
        # Legacy/corrected records still use the same language stage, not a
        # separate Agent-only language heuristic. Explicit corrections are intentional text.
        if not language or preprocessing.get("source_revision", 1) != language_source["revision"]:
            language = preprocess(language_source["text"], "correction").reply_language
        message += language_instruction(language)
        if original_question:
            if source["metadata"].get("resolution") == "declined":
                message += "\nHinter 已暂不提供本项资料，请给出有限结论，不再次请求同一缺口。"
            message += (
                "\n这是明确关联的 Hinter 补充后续答。原始请求："
                + original_question["text"]
                + "\n本次已公开补充："
                + source["text"]
                + "\n请直接继续回答原始请求，保留单位、时间、范围与来源。"
                "未提供的内部数据仍然未知；不能把补充描述成已联网独立核验。"
            )
        text_parts = {}
        current_message = None
        if run["attempt"] == 1:
            stream = client.stream(
                message, thread_id=run["thread_id"], **context_for(run), recursion_limit=100
            )
        else:
            emit(run, "assistant.reset", {"attempt": run["attempt"]})
            stream = resume_stream(client, run, message)
        for frame in stream:
            current_grant(context_for(run))
            if frame.type != "messages-tuple":
                continue
            data = frame.data
            if data.get("type") == "ai":
                if data.get("content"):
                    key = data.get("id") or "answer"
                    if current_message != key:
                        text_parts.clear()
                        current_message = key
                    text_parts[key] = text_parts.get(key, "") + data["content"]
                    emit(
                        run,
                        "assistant.snapshot",
                        {
                            "text": public_answer(text_parts[key], partial=True),
                            "message_id": key,
                        },
                    )
                for tc in data.get("tool_calls", []):
                    emit(run, "tool.started", {"name": tc["name"], "tool_call_id": tc.get("id")})
            elif data.get("type") == "tool":
                text_parts.clear()
                current_message = None
                emit(run, "assistant.reset", {})
                # Keep results inside the checkpoint, expose only bounded public result previews.
                failed = data.get("status") == "error"
                if data.get("name") in {"web_search", "web_fetch", "request_hinter"}:
                    try:
                        result = json.loads(data.get("content", ""))
                        failed = failed or (isinstance(result, dict) and result.get("ok") is False)
                    except (ValueError, TypeError):
                        pass
                emit(
                    run,
                    "tool.failed" if failed else "tool.completed",
                    {
                        "name": data.get("name"),
                        "tool_call_id": data.get("tool_call_id"),
                        "result": str(data.get("content", ""))[:4000],
                    },
                )
        output = public_answer("\n\n".join(v for v in text_parts.values() if v.strip()))
        if not output.strip():
            raise RuntimeError("EMPTY_AGENT_OUTPUT")
        # Main output becomes visible before the secondary extraction request.
        emit(run, "assistant.completed", {"text": output})
        complete(run, "completed", output)
    except AnswerRestricted as denied:
        text = refusal(denied.language)
        if run["kind"] == "interactive":
            emit(run, "assistant.completed", {"text": text}, policy_refusal=True)
        complete(run, "completed", text, policy_blocked=True)
    except InterruptedRun as e:
        complete(run, "cancelled", error=str(e))
    except PermissionError as exc:
        log.warning("Run %s denied: %s", run["id"], str(exc)[:120])
        complete(run, "cancelled", error="AUTHORIZATION_REVOKED")
    except Exception as exc:
        # Never publish provider exception text: it can contain URLs/credentials.
        log.error("Run %s failed: %s", run["id"], type(exc).__name__)
        complete(run, "failed", error="AGENT_RUNTIME_ERROR")
    finally:
        finished.set()
        keeper.join(timeout=6)
        client._checkpointer.release_run(run)
        ACTIVE_RUN.reset(bound_run)


def resume_stream(client, run, message):
    """Pinned 2.1.0 integration: resume pending native nodes without a second HumanMessage."""
    from deerflow.client import StreamEvent

    ctx = context_for(run)
    ctx.update(thread_id=run["thread_id"], run_id=run["id"])
    config = client._get_runnable_config(run["thread_id"], recursion_limit=100)
    client._ensure_agent(config, context=ctx)
    checkpoint = client._checkpointer.get_tuple(config)
    state = client._agent.get_state(config)
    if (
        not state.values
        or not checkpoint
        or checkpoint.metadata.get("duet_app_run_id") != run["id"]
    ):
        yield from client.stream(
            message, thread_id=run["thread_id"], **context_for(run), recursion_limit=100
        )
        return
    if state.next:
        for _ in client._agent.stream(None, config=config, context=ctx, stream_mode="values"):
            current_grant(ctx)
        state = client._agent.get_state(config)
    # Last human boundary identifies the recovered turn; previous turns stay out.
    messages = state.values.get("messages", [])
    start = max((i for i, m in enumerate(messages) if m.type == "human"), default=-1)
    for m in messages[start + 1 :]:
        if m.type == "ai" and isinstance(m.content, str) and m.content:
            yield StreamEvent(
                type="messages-tuple", data={"type": "ai", "id": m.id, "content": m.content}
            )
        elif m.type == "tool":
            yield StreamEvent(
                type="messages-tuple",
                data={
                    "type": "tool",
                    "name": m.name,
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                },
            )


def execute_child(run):
    # Isolate native model/tool code so a stuck socket/thread can actually be stopped.
    if hasattr(os, "setsid"):
        os.setsid()
    logging.basicConfig(level=logging.WARNING)
    execute(make_client(), run)


def stop_child(process):
    if process.is_alive():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
        process.join(timeout=2)
    if process.is_alive():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.join(timeout=2)


def supervise(run):
    total_limit = min(1800, max(30, int(os.getenv("AGENT_RUN_TIMEOUT_SECONDS", "120"))))
    idle_limit = min(total_limit, max(15, int(os.getenv("AGENT_RUN_IDLE_TIMEOUT_SECONDS", "45"))))
    process = multiprocessing.get_context("spawn").Process(target=execute_child, args=(run,))
    process.start()
    started = time.monotonic()
    last_health = started
    outcome, reason = None, None
    try:
        while process.is_alive():
            process.join(timeout=0.5)
            with transaction() as c:
                value = c.execute(select(runs).where(runs.c.id == run["id"])).mappings().one()
                if value["status"] != "running" or value["lease_token"] != run["lease_token"]:
                    break
                try:
                    verify(c, run)
                except InterruptedRun as exc:
                    outcome, reason = "cancelled", str(exc)
                    break
                from .db import events

                last_event = c.execute(
                    select(events.c.created_at)
                    .where(events.c.run_id == run["id"])
                    .order_by(events.c.seq.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if time.monotonic() - started >= total_limit:
                    outcome, reason = "failed", "AGENT_RUN_TIMEOUT"
                    break
                if now() - (last_event or run["updated_at"]) >= idle_limit:
                    outcome, reason = "failed", "AGENT_RUN_IDLE_TIMEOUT"
                    break
                if time.monotonic() - last_health >= 10:
                    repo_ready(c)
                    last_health = time.monotonic()
            if STOP.is_set():
                # Leave the lease/checkpoint recoverable on an ordinary deployment restart.
                return
        if not outcome:
            with transaction() as c:
                value = c.execute(select(runs).where(runs.c.id == run["id"])).mappings().one()
                if value["status"] == "running" and value["lease_token"] == run["lease_token"]:
                    outcome, reason = "failed", "AGENT_PROCESS_EXITED"
    finally:
        stop_child(process)
        process.close()
        if STOP.is_set():
            with transaction() as c:
                c.execute(
                    update(runs)
                    .where(
                        runs.c.id == run["id"],
                        runs.c.lease_token == run["lease_token"],
                        runs.c.status == "running",
                    )
                    .values(lease_until=now() - 1)
                )
                c.execute(
                    update(threads)
                    .where(threads.c.id == run["thread_id"], threads.c.lease_run_id == run["id"])
                    .values(lease_until=now() - 1)
                )
    if outcome:
        complete(run, outcome, error=reason)


def main():
    logging.basicConfig(level=logging.WARNING)
    if os.getenv("DUET_WORKER_TRACEBACK") == "1":
        import faulthandler

        faulthandler.dump_traceback_later(40, repeat=True)
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    with transaction() as c:
        repo_ready(c)
    if WORK_KIND == "interactive":

        def maintain_name_pool():
            while not STOP.is_set():
                try:
                    names.refill_if_low()
                except Exception as exc:
                    log.warning("Flower-name refill failed: %s", type(exc).__name__)
                STOP.wait(60)

        threading.Thread(target=maintain_name_pool, name="flower-name-refill", daemon=True).start()
    last_ready = time.monotonic()
    while not STOP.is_set():
        try:
            run = claim()
            if run:
                supervise(run)
            else:
                STOP.wait(0.5)
            if time.monotonic() - last_ready > 10:
                with transaction() as c:
                    repo_ready(c)
                last_ready = time.monotonic()
        except Exception as exc:
            log.error("Worker cycle failed: %s", type(exc).__name__)
            STOP.wait(2)


if __name__ == "__main__":
    main()
