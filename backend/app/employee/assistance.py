"""Owner-scoped human assistance; only explicit Hinter disclosure becomes model input."""

import hashlib
import json

from sqlalchemy import and_, func, insert, or_, select, update

from . import auth
from . import repository as repo
from .db import assistance_requests as requests
from .db import commands, now, records, runs, threads, transaction, uid, users, workspaces


def create_request(runtime, summary, needed_information, reason):
    from .agent_tools import current_grant

    summary, needed_information = summary.strip(), needed_information.strip()
    if (
        not summary
        or len(summary) > 500
        or not needed_information
        or len(needed_information) > 4000
    ):
        raise ValueError("Provide a concise summary and the exact missing information")
    with transaction() as c:
        run, actor, _ = current_grant(runtime.context, c)
        repo.thread_access(c, actor, run["thread_id"], write=True, lock=True)
        run, _, _ = current_grant(runtime.context, c, lock=True)
        source = c.execute(select(records).where(records.c.id == run["record_id"])).mappings().one()
        if source["withdrawn"] or source["visibility"] != "user_shared":
            raise PermissionError("Only visible user context can request assistance")
        if source["revision"] != run["source_revision"]:
            return {"ok": False, "error": "SOURCE_CHANGED"}
        if source["metadata"].get("resolution") == "declined":
            return {
                "ok": True,
                "status": "declined",
                "request_id": source["metadata"].get("assistance_request_id"),
                "message": "Hinter 已暂不提供本项资料，请说明信息缺口，不重复登记同一求助。",
            }
        previous = (
            c.execute(
                select(requests).where(
                    or_(
                        requests.c.run_id == run["id"],
                        and_(
                            requests.c.thread_id == run["thread_id"],
                            requests.c.source_record_id == source["id"],
                            requests.c.source_revision == source["revision"],
                        ),
                    )
                )
            ).mappings().first()
        )
        if previous:
            return {"ok": True, "request_id": previous["id"], "status": previous["status"]}
        value = {
            "id": uid("assistance"),
            "thread_id": run["thread_id"],
            "run_id": run["id"],
            "source_record_id": source["id"],
            "source_revision": source["revision"],
            "summary": summary,
            "needed_information": needed_information,
            "reason": reason,
            "status": "pending",
            "version": 1,
            "created_at": now(),
            "updated_at": now(),
        }
        c.execute(insert(requests).values(**value))
        repo.event(c, run, "hinter.requested", {"request_id": value["id"], "status": "pending"})
        auth.audit_event(c, actor["id"], "hinter.requested", value["id"], {"run_id": run["id"]})
        return {
            "ok": True,
            "request_id": value["id"],
            "status": "pending",
            "message": (
                "已登记 Hinter 待补充事项。先说明已知部分与缺口，"
                "不等待或猜测内部数据；公开补充后将自动继续。"
            ),
        }


def list_requests(actor, *, thread_id=None, request_id=None, page=1, page_size=20, pending=False):
    with transaction() as c:
        if thread_id:
            repo.thread_access(c, actor, thread_id)
        else:
            auth.require(actor, "workspace.supervise")
        query = (
            select(
                requests,
                threads.c.workspace_id,
                users.c.display_name.label("owner_name"),
                records.c.text.label("original_text"),
                runs.c.status.label("continuation_status"),
            )
            .join(threads, threads.c.id == requests.c.thread_id)
            .join(workspaces, workspaces.c.id == threads.c.workspace_id)
            .join(users, users.c.id == workspaces.c.owner_user_id)
            .join(records, records.c.id == requests.c.source_record_id)
            .outerjoin(runs, runs.c.id == requests.c.continuation_run_id)
            .where(
                records.c.withdrawn.is_(False),
                records.c.visibility == "user_shared",
                records.c.revision == requests.c.source_revision,
            )
        )
        if thread_id:
            query = query.where(requests.c.thread_id == thread_id)
        if request_id:
            query = query.where(requests.c.id == request_id)
        if pending:
            query = query.where(requests.c.status == "pending")
        total = c.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        values = [
            dict(row)
            for row in c.execute(
                query.order_by(
                    (requests.c.status == "pending").desc(), requests.c.created_at.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).mappings()
        ]
        if "hinter_admin" not in actor["roles"]:
            for value in values:
                for key in ("needed_information", "reason", "resolved_by_user_id", "owner_name"):
                    value.pop(key, None)
        return {"requests": values, "total": total, "page": page, "page_size": page_size}


def get_request(actor, request_id):
    auth.require(actor, "guidance.write")
    with transaction() as c:
        thread_id = c.execute(
            select(requests.c.thread_id).where(requests.c.id == request_id)
        ).scalar_one_or_none()
        if not thread_id:
            auth.fail(404, "NOT_FOUND", "求助事项不存在。")
        repo.thread_access(c, actor, thread_id)
    result = list_requests(actor, thread_id=thread_id, request_id=request_id, page_size=1)
    if not result["requests"]:
        auth.fail(409, "SOURCE_CHANGED", "原问题已撤回或修订，请退出补充后重新选择待补充事项。")
    return result["requests"][0]


def resolve_request(actor, request_id, body, key):
    auth.require(actor, "guidance.write")
    if not key or len(key) > 100:
        auth.fail(422, "IDEMPOTENCY_KEY_REQUIRED", "请使用有效的请求标识。")
    content = body.text.strip()
    if body.action == "provide" and not content:
        auth.fail(422, "VALIDATION_ERROR", "请填写允许公开给用户与 Agent 的资料。")
    digest = hashlib.sha256(
        json.dumps([actor["id"], body.model_dump()], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    with transaction() as c:
        existing = c.execute(select(requests).where(requests.c.id == request_id)).mappings().first()
        if not existing:
            auth.fail(404, "NOT_FOUND", "求助事项不存在。")
        thread, ws = repo.thread_access(c, actor, existing["thread_id"], write=True, lock=True)
        item = (
            c.execute(select(requests).where(requests.c.id == request_id).with_for_update())
            .mappings()
            .one()
        )
        operation = f"assistance.resolve:{request_id}"
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
            if previous["request_hash"] != digest:
                auth.fail(409, "IDEMPOTENCY_CONFLICT", "同一请求对应不同内容。")
            return previous["response"]
        retry = body.action == "retry"
        allowed_status = (
            item["status"] in {"provided", "declined"} if retry else item["status"] == "pending"
        )
        if not allowed_status or item["version"] != body.expected_version:
            auth.fail(409, "VERSION_CONFLICT", "该事项已更新，请重新加载后查看。")
        source = (
            c.execute(
                select(records).where(records.c.id == item["source_record_id"]).with_for_update()
            )
            .mappings()
            .one()
        )
        if source["withdrawn"] or source["revision"] != item["source_revision"]:
            auth.fail(409, "SOURCE_CHANGED", "原问题已修订，请处理新版本对应的事项。")
        if retry:
            old_run = (
                c.execute(
                    select(runs).where(runs.c.id == item["continuation_run_id"]).with_for_update()
                )
                .mappings()
                .one()
            )
            if old_run["status"] not in {"failed", "cancelled"}:
                auth.fail(409, "ALREADY_CONTINUING", "续答正在处理或已完成，无需重复提交。")
            guidance = (
                c.execute(select(records).where(records.c.id == item["guidance_record_id"]))
                .mappings()
                .one()
            )
            if guidance["withdrawn"] or guidance["visibility"] != "user_shared":
                auth.fail(409, "SOURCE_CHANGED", "公开补充已不可用，请检查原记录。")
            status = item["status"]
        else:
            status = "provided" if body.action == "provide" else "declined"
            public_text = (
                content
                if status == "provided"
                else "Hinter 暂未提供所需资料，请如实说明信息缺口，不猜测内部数据。"
            )
            guidance = repo.append_record(
                c,
                thread,
                actor_id=actor["id"],
                role="hinter",
                kind="guidance",
                text=public_text,
                visibility="user_shared",
                source_id=source["id"],
                metadata={
                    "assistance_request_id": request_id,
                    "resolution": status,
                    "original_revision": source["revision"],
                },
            )
        continuation = repo.queue_run(
            c,
            actor,
            thread["id"],
            ws["owner_user_id"],
            guidance["id"],
            parent_run_id=item["run_id"],
            source_revision=guidance["revision"],
        )
        c.execute(
            update(requests)
            .where(requests.c.id == request_id)
            .values(
                status=status,
                version=item["version"] + 1,
                resolved_by_user_id=actor["id"],
                guidance_record_id=guidance["id"],
                continuation_run_id=continuation["id"],
                updated_at=now(),
            )
        )
        auth.audit_event(
            c,
            actor["id"],
            "hinter." + ("retried" if retry else status),
            request_id,
            {"guidance_record_id": guidance["id"], "run_id": continuation["id"]},
        )
        result = {
            "request_id": request_id,
            "status": status,
            "record_id": guidance["id"],
            "run_id": continuation["id"],
            "version": item["version"] + 1,
        }
        c.execute(
            insert(commands).values(
                owner_user_id=ws["owner_user_id"],
                operation=operation,
                key=key,
                request_hash=digest,
                response=result,
            )
        )
        return result
