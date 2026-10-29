"""Authenticated BFF. Agent execution belongs to the independent DeerFlow worker."""

import asyncio
import json
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import assistance, auth, names
from . import repository as repo
from .db import (
    assistance_requests,
    audit,
    auth_sessions,
    events,
    locks,
    memories,
    now,
    projections,
    records,
    revisions,
    runs,
    schema,
    threads,
    transaction,
    users,
    workspaces,
)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Register(Body):
    phone: str
    email: str
    password: str = Field(min_length=12, max_length=256)
    display_name: str | None = Field(default=None, max_length=120)


class Login(Body):
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(max_length=256)


class ChangePassword(Body):
    old_password: str = Field(max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class ThreadCreate(Body):
    workspace_id: str
    title: str = Field(default="新会议", max_length=160)
    profile: str = "meeting_assistant"


class InputContent(Body):
    kind: Literal["text", "transcript", "correction"] = "text"
    text: str = Field(min_length=1, max_length=60000)
    source_record_id: str | None = None


class Input(Body):
    agent_id: str = "personal-employee"
    profile: str = "meeting_assistant"
    input: InputContent
    presentation_mode: str = "questions"


class Roles(Body):
    roles: list[str]
    expected_version: int


class Status(Body):
    status: str
    expected_version: int


class Memory(Body):
    name: str = Field(min_length=1, max_length=160)
    text: str = Field(max_length=60000)
    visibility: str = "user_shared"
    expected_revision: int | None = None


class Guidance(Body):
    text: str = Field(min_length=1, max_length=20000)
    visibility: Literal["admin_private", "user_shared"] = "admin_private"
    workspace_id: str | None = None


class AnswerPolicyCreate(Body):
    aliases: list[str] = Field(min_length=1, max_length=20)
    scope: Literal["talker"] = "talker"


class AnswerPolicyUpdate(Body):
    enabled: bool
    expected_version: int = Field(ge=1)


class AssistanceResolution(Body):
    action: Literal["provide", "decline", "retry"]
    text: str = Field(default="", max_length=20000)
    expected_version: int = Field(ge=1)


class Correction(Body):
    text: str = Field(min_length=1, max_length=60000)
    expected_revision: int


class Bind(Body):
    workspace_id: str
    user_id: str


def create_app():
    app = FastAPI(title="Duet Personal Employee", version="1.0.0")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default error includes the rejected input (potential password).
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "VALIDATION_ERROR",
                    "message": "请检查输入字段、长度和格式。",
                    "fields": [".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()],
                }
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "数据服务暂时不可用，请稍后重试。",
                }
            },
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def health():
        return {"status": "ok", "service": "duet-personal-employee"}

    @app.get("/readyz")
    def ready():
        with transaction() as c:
            version = c.execute(select(func.max(schema.c.version))).scalar()
            heartbeat = c.execute(
                select(audit.c.created_at)
                .where(audit.c.action == "worker.ready")
                .order_by(audit.c.created_at.desc())
                .limit(1)
            ).scalar()
        if version != 6 or not heartbeat or heartbeat < now() - 45:
            raise HTTPException(
                503, {"code": "PROVIDER_NOT_READY", "message": "数据库或 Agent worker 尚未就绪。"}
            )
        return {
            "status": "ready",
            "schema_version": version,
            "runtime": "deerflow-2.1.0",
            "persistence": "mysql",
        }

    @app.post("/api/v1/auth/name-suggestion")
    def name_suggestion(request: Request):
        auth.public_mutation(request)
        auth.rate_limit(request, "name-suggestion")
        with transaction() as c:
            suggestion = names.claim(c)
        if suggestion is None:
            auth.fail(503, "NAME_POOL_EXHAUSTED", "暂时没有可用花名，请自行填写用户名。")
        return {"display_name": suggestion}

    @app.post("/api/v1/auth/register", status_code=201)
    def register(body: Register, request: Request):
        auth.public_mutation(request)
        auth.rate_limit(request, "register")
        phone, email = auth.phone(body.phone), auth.email(body.email)
        encoded = auth.password(body.password)
        try:
            with transaction() as c:
                requested_name = body.display_name.strip() if body.display_name else ""
                if not requested_name:
                    display_name = names.claim(c)
                    if display_name is None:
                        auth.fail(
                            503, "NAME_POOL_EXHAUSTED", "暂时没有可用花名，请自行填写用户名。"
                        )
                else:
                    display_name = requested_name
                user_id, workspace_id = auth.create_user(
                    c,
                    phone_value=phone,
                    email_value=email,
                    display_name=display_name,
                    password_hash=encoded,
                )
                auth.audit_event(c, user_id, "account.registered", user_id)
        except IntegrityError:
            auth.fail(409, "ACCOUNT_IDENTIFIER_EXISTS", "该手机或邮箱已注册，请使用已有账号登录。")
        return {
            "user_id": user_id,
            "display_name": display_name,
            "default_workspace_id": workspace_id,
            "roles": ["talker"],
            "login_required": True,
        }

    @app.post("/api/v1/auth/login")
    def login(body: Login, request: Request, response: Response):
        auth.public_mutation(request)
        identifier = body.identifier.strip().casefold()
        auth.rate_limit(request, "login", identifier)
        if re.fullmatch(r"1[3-9]\d{9}", identifier):
            identifier = "+86" + identifier
        if identifier.startswith("+"):
            try:
                identifier = auth.phone(identifier)
            except HTTPException:
                identifier = "invalid-phone"
        with transaction() as c:
            user = (
                c.execute(
                    select(users).where(
                        or_(
                            users.c.phone == identifier,
                            users.c.email == identifier,
                            users.c.login_alias == identifier,
                        )
                    )
                )
                .mappings()
                .first()
            )
            valid = auth.check_password(
                user["password_hash"] if user else auth.DUMMY_HASH, body.password
            )
            if not user or not valid or user["status"] != "active":
                auth.fail(401, "INVALID_CREDENTIALS", "账号或密码错误。")
            actor = auth.establish(c, user, response)
            auth.audit_event(c, user["id"], "account.login", user["id"])
        return auth.describe(actor)

    @app.get("/api/v1/auth/me")
    def me(request: Request):
        return auth.describe(auth.session_user(request, allow_password_change=True))

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, response: Response):
        auth.origin_check(request)
        if request.cookies.get(auth.COOKIE):
            try:
                actor = auth.session_user(request, allow_password_change=True)
            except HTTPException as e:
                if e.status_code != 401:
                    raise
            else:
                with transaction() as c:
                    c.execute(
                        delete(auth_sessions).where(
                            auth_sessions.c.digest == actor["session_digest"]
                        )
                    )
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/v1/auth/change-password")
    def change_password(body: ChangePassword, request: Request, response: Response):
        actor = auth.session_user(request, allow_password_change=True)
        if not auth.check_password(actor["password_hash"], body.old_password):
            auth.fail(401, "INVALID_CREDENTIALS", "当前密码不正确。")
        if body.old_password == body.new_password:
            auth.fail(422, "VALIDATION_ERROR", "新密码应与当前密码不同。")
        encoded = auth.password(body.new_password)
        with transaction() as c:
            fresh = (
                c.execute(select(users).where(users.c.id == actor["id"]).with_for_update())
                .mappings()
                .one()
            )
            if fresh["auth_version"] != actor["auth_version"]:
                auth.fail(409, "VERSION_CONFLICT", "账号已变化，请重新登录。")
            values = {
                "password_hash": encoded,
                "must_change_password": False,
                "auth_version": actor["auth_version"] + 1,
                "version": fresh["version"] + 1,
            }
            c.execute(update(users).where(users.c.id == actor["id"]).values(**values))
            c.execute(delete(auth_sessions).where(auth_sessions.c.user_id == actor["id"]))
            updated = auth.establish(c, {**actor, **values}, response)
            auth.audit_event(c, actor["id"], "password.changed", actor["id"])
        return auth.describe(updated)

    @app.get("/api/v1/agents")
    def agents(request: Request):
        auth.session_user(request)
        return {
            "agents": [
                {
                    "id": "personal-employee",
                    "name": "Duet · 个人员工",
                    "description": "一起记录会议、梳理问题、查找资料与跟进行动。",
                    "runtime": "DeerFlow 2.1",
                    "profiles": ["meeting_assistant"],
                    "presentation_modes": ["questions"],
                    "capabilities": ["会话记录", "问题视图", "个人记忆", "资料检索"],
                }
            ]
        }

    @app.get("/api/v1/workspaces")
    def workspace_list(request: Request):
        actor = auth.session_user(request)
        with transaction() as c:
            return {
                "workspaces": [
                    dict(r)
                    for r in c.execute(
                        select(workspaces).where(workspaces.c.owner_user_id == actor["id"])
                    ).mappings()
                ]
            }

    @app.get("/api/v1/threads")
    def thread_list(request: Request, workspace_id: str, limit: int = 50):
        actor = auth.session_user(request)
        with transaction() as c:
            repo.workspace_access(c, actor, workspace_id)
            return {
                "threads": [
                    dict(r)
                    for r in c.execute(
                        select(threads)
                        .where(threads.c.workspace_id == workspace_id)
                        .order_by(threads.c.created_at.desc())
                        .limit(min(max(limit, 1), 100))
                    ).mappings()
                ]
            }

    @app.post("/api/v1/threads", status_code=201)
    def new_thread(body: ThreadCreate, request: Request):
        return repo.create_thread(
            auth.session_user(request), body.workspace_id, body.title, body.profile
        )

    @app.get("/api/v1/threads/{thread_id}")
    def thread_get(thread_id: str, request: Request):
        actor = auth.session_user(request)
        with transaction() as c:
            thread, ws = repo.thread_access(c, actor, thread_id)
            recent = (
                select(runs.c.id)
                .where(runs.c.thread_id == thread_id, runs.c.kind == "interactive")
                .order_by(runs.c.created_at.desc())
                .limit(50)
                .subquery()
            )
            active = [
                {
                    k: r[k]
                    for k in (
                        "id",
                        "thread_id",
                        "record_id",
                        "status",
                        "attempt",
                        "cancel_requested",
                        "error_code",
                        "created_at",
                        "updated_at",
                    )
                }
                for r in c.execute(
                    select(runs)
                    .where(
                        runs.c.thread_id == thread_id,
                        runs.c.kind == "interactive",
                        or_(
                            runs.c.status.in_(["queued", "running"]),
                            runs.c.id.in_(select(recent.c.id)),
                        ),
                    )
                    .order_by(runs.c.created_at.desc())
                ).mappings()
            ]
            return {
                "thread": {
                    k: v for k, v in thread.items() if k not in ("lease_run_id", "lease_until")
                },
                "workspace": ws,
                "runs": active,
            }

    @app.get("/api/v1/threads/{thread_id}/records")
    def records_get(thread_id: str, request: Request, after_seq: int = 0):
        with transaction() as c:
            items = repo.visible_records(
                c, auth.session_user(request), thread_id, max(after_seq, 0)
            )
            return {"records": items, "next_seq": items[-1]["seq"] if items else after_seq}

    @app.post("/api/v1/threads/{thread_id}/inputs", status_code=202)
    def input_create(
        thread_id: str,
        body: Input,
        request: Request,
        idempotency_key: str | None = Header(default=None),
    ):
        return repo.submit_input(auth.session_user(request), thread_id, body, idempotency_key)

    @app.get("/api/v1/threads/{thread_id}/projections")
    def projections_get(thread_id: str, request: Request, mode: str = "questions"):
        if mode != "questions":
            auth.fail(422, "PRESENTATION_MODE_UNSUPPORTED", "当前仅支持 questions。")
        actor = auth.session_user(request)
        with transaction() as c:
            repo.thread_access(c, actor, thread_id)
            q = (
                select(projections)
                .join(records, records.c.id == projections.c.record_id)
                .where(
                    records.c.thread_id == thread_id,
                    records.c.withdrawn.is_(False),
                    records.c.visibility == "user_shared",
                )
                .order_by(records.c.seq)
                .limit(200)
            )
            from .question_projection import deduplicate_questions

            values = []
            for row in c.execute(q).mappings():
                value = dict(row)
                value["items"] = deduplicate_questions(value["items"] or [])
                values.append(value)
            return {"projections": values}

    @app.post("/api/v1/threads/{thread_id}/projections/rebuild", status_code=202)
    def projection_rebuild(thread_id: str, request: Request):
        actor = auth.session_user(request)
        queued = 0
        with transaction() as c:
            _, ws = repo.thread_access(c, actor, thread_id, write=True, lock=True)
            source = c.execute(
                select(records)
                .where(
                    records.c.thread_id == thread_id,
                    records.c.role == "user",
                    records.c.visibility == "user_shared",
                    records.c.withdrawn.is_(False),
                )
                .order_by(records.c.seq)
                .limit(200)
            ).mappings()
            for record in source:
                existing = (
                    c.execute(
                        select(projections).where(
                            projections.c.record_id == record["id"],
                            projections.c.kind == "questions",
                        )
                    )
                    .mappings()
                    .first()
                )
                if (
                    existing
                    and existing["status"] == "ready"
                    and existing["source_revision"] == record["revision"]
                ):
                    continue
                active = c.execute(
                    select(runs.c.id).where(
                        runs.c.record_id == record["id"],
                        runs.c.kind == "projection",
                        runs.c.source_revision == record["revision"],
                        runs.c.status.in_(["queued", "running"]),
                    )
                ).first()
                if active:
                    continue
                if existing:
                    c.execute(
                        update(projections)
                        .where(
                            projections.c.record_id == record["id"],
                            projections.c.kind == "questions",
                        )
                        .values(status="pending", source_revision=record["revision"], items=[])
                    )
                else:
                    c.execute(
                        insert(projections).values(
                            record_id=record["id"],
                            kind="questions",
                            source_revision=record["revision"],
                            status="pending",
                            items=[],
                        )
                    )
                repo.queue_run(
                    c,
                    actor,
                    thread_id,
                    ws["owner_user_id"],
                    record["id"],
                    kind="projection",
                    source_revision=record["revision"],
                )
                queued += 1
        return {"queued": queued}

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: str, request: Request):
        actor = auth.session_user(request)
        with transaction() as c:
            run = repo.run_access(c, actor, run_id)
            repo.thread_access(c, actor, run["thread_id"], lock=True)
            run = dict(
                c.execute(select(runs).where(runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            if run["status"] not in repo.TERMINAL:
                c.execute(update(runs).where(runs.c.id == run_id).values(cancel_requested=True))
                if run["status"] == "queued":
                    c.execute(
                        update(runs)
                        .where(runs.c.id == run_id, runs.c.status == "queued")
                        .values(status="cancelled", updated_at=now())
                    )
                    repo.event(c, run, "run.cancelled", {"reason": "explicit_cancel"})
                    run["status"] = "cancelled"
            return {"run_id": run_id, "status": run["status"], "cancel_requested": True}

    @app.get("/api/v1/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, after_seq: int = 0, stream: bool = False):
        def read():
            actor = auth.session_user(request)
            with transaction() as c:
                run = repo.run_access(c, actor, run_id)
                items = [
                    dict(r)
                    for r in c.execute(
                        select(events)
                        .where(events.c.run_id == run_id, events.c.seq > after_seq)
                        .order_by(events.c.seq)
                        .limit(500)
                    ).mappings()
                ]
                return run, items

        run, items = await asyncio.to_thread(read)
        if not stream:
            return {"events": items, "status": run["status"]}

        async def sse():
            nonlocal after_seq
            while not await request.is_disconnected():
                try:
                    run, values = await asyncio.to_thread(read)
                except HTTPException:
                    yield "event: auth.expired\ndata: {}\n\n"
                    return
                for item in values:
                    after_seq = item["seq"]
                    yield (
                        f"id: {item['seq']}\nevent: {item['type']}\n"
                        f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                    )
                if run["status"] in repo.TERMINAL and not values:
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            sse(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.patch("/api/v1/records/{record_id}")
    def correct_record(record_id: str, body: Correction, request: Request):
        actor = auth.session_user(request)
        with transaction() as c:
            old = c.execute(select(records).where(records.c.id == record_id)).mappings().first()
            if not old:
                auth.fail(404, "NOT_FOUND", "记录不存在。")
            thread, ws = repo.thread_access(c, actor, old["thread_id"], write=True, lock=True)
            current = (
                c.execute(select(records).where(records.c.id == record_id).with_for_update())
                .mappings()
                .one()
            )
            if current["role"] != "user":
                auth.require(actor, "guidance.write")
            if current["visibility"] != "user_shared":
                auth.require(actor, "guidance.write")
            if current["revision"] != body.expected_revision:
                auth.fail(409, "VERSION_CONFLICT", "记录已有新版本，请刷新后重试。")
            revision = current["revision"] + 1
            c.execute(
                insert(revisions).values(
                    record_id=record_id,
                    revision=revision,
                    text=body.text,
                    actor_user_id=actor["id"],
                    created_at=now(),
                )
            )
            c.execute(
                update(records)
                .where(records.c.id == record_id)
                .values(text=body.text, revision=revision)
            )
            c.execute(
                update(projections)
                .where(projections.c.record_id == record_id)
                .values(status="pending", source_revision=revision, items=[])
            )
            if current["role"] == "hinter" and current["kind"] == "guidance":
                from .wiki import sync_hint

                sync_hint(c, {**dict(current), "text": body.text, "revision": revision})
            auth.audit_event(c, actor["id"], "record.corrected", record_id, {"revision": revision})
            c.execute(
                update(assistance_requests)
                .where(
                    assistance_requests.c.source_record_id == record_id,
                    assistance_requests.c.status == "pending",
                )
                .values(
                    status="superseded", updated_at=now(), version=assistance_requests.c.version + 1
                )
            )
            run = (
                repo.queue_run(
                    c, actor, thread["id"], ws["owner_user_id"], record_id, source_revision=revision
                )
                if current["visibility"] == "user_shared"
                else None
            )
        return {"record_id": record_id, "revision": revision, "run_id": run["id"] if run else None}

    @app.get("/api/v1/admin/assistance-requests")
    def assistance_inbox(request: Request, page: int = 1, page_size: int = 20):
        return assistance.list_requests(
            auth.session_user(request),
            page=max(1, page),
            page_size=min(50, max(1, page_size)),
            pending=True,
        )

    @app.get("/api/v1/threads/{thread_id}/assistance-requests")
    def assistance_for_thread(thread_id: str, request: Request):
        return assistance.list_requests(
            auth.session_user(request), thread_id=thread_id, page_size=30
        )

    @app.get("/api/v1/assistance-requests/{request_id}")
    def assistance_detail(request_id: str, request: Request):
        return assistance.get_request(auth.session_user(request), request_id)

    @app.post("/api/v1/assistance-requests/{request_id}/resolve")
    def assistance_resolve(
        request_id: str,
        body: AssistanceResolution,
        request: Request,
        idempotency_key: str | None = Header(default=None),
    ):
        return assistance.resolve_request(
            auth.session_user(request), request_id, body, idempotency_key
        )

    @app.get("/api/v1/admin/workspaces/{workspace_id}/answer-policies")
    def answer_policies_get(workspace_id: str, request: Request):
        from . import answer_policy

        return {"policies": answer_policy.list_rules(auth.session_user(request), workspace_id)}

    @app.post("/api/v1/admin/workspaces/{workspace_id}/answer-policies", status_code=201)
    def answer_policy_create(workspace_id: str, body: AnswerPolicyCreate, request: Request):
        from . import answer_policy

        with transaction() as c:
            return answer_policy.create(
                c, auth.session_user(request), workspace_id, body.aliases, body.scope
            )

    @app.patch("/api/v1/admin/answer-policies/{policy_id}")
    def answer_policy_update(policy_id: str, body: AnswerPolicyUpdate, request: Request):
        from . import answer_policy

        return answer_policy.set_enabled(
            auth.session_user(request), policy_id, body.enabled, body.expected_version
        )

    @app.post("/api/v1/threads/{thread_id}/guidance", status_code=201)
    def guidance(thread_id: str, body: Guidance, request: Request):
        actor = auth.session_user(request)
        auth.require(actor, "guidance.write")
        with transaction() as c:
            thread, workspace = repo.thread_access(c, actor, thread_id, write=True, lock=True)
            if body.workspace_id is not None and body.workspace_id != workspace["id"]:
                auth.fail(
                    409,
                    "GUIDANCE_TARGET_CHANGED",
                    "提示对象与会话不一致，请重新选择 Talker 后保存。",
                )
            value = repo.append_record(
                c,
                thread,
                actor_id=actor["id"],
                role="hinter",
                kind="guidance",
                text=body.text,
                visibility=body.visibility,
            )
            auth.audit_event(
                c, actor["id"], "guidance.created", value["id"], {"visibility": body.visibility}
            )
            from .answer_policy import extract_ban

            return {
                **value,
                "memory_layer": "prohibition" if extract_ban(body.text) else "long_term",
            }

    @app.get("/api/v1/workspaces/{workspace_id}/memory")
    def memory_get(workspace_id: str, request: Request):
        actor = auth.session_user(request)
        with transaction() as c:
            repo.workspace_access(c, actor, workspace_id)
            from .answer_policy import origin, owner_scope
            from .wiki import public_source_guard

            q = select(memories).where(owner_scope(c, workspace_id))
            if "hinter_admin" not in actor["roles"]:
                from .wiki import public_source_guard

                q = q.where(memories.c.visibility == "user_shared", public_source_guard())

            q = q.where(origin() != "answer_policy")
            documents = [dict(r) for r in c.execute(q).mappings()]
            if "hinter_admin" not in actor["roles"]:
                for document in documents:
                    document["metadata"] = {
                        key: value
                        for key, value in document["metadata"].items()
                        if key != "revisions"
                    }
            return {"documents": documents}

    @app.put("/api/v1/workspaces/{workspace_id}/memory")
    def memory_put(workspace_id: str, body: Memory, request: Request):
        return repo.put_memory(
            auth.session_user(request),
            workspace_id,
            body.name,
            body.text,
            body.visibility,
            body.expected_revision,
        )

    @app.get("/api/v1/admin/users")
    def admin_users(request: Request, q: str = "", page: int = 1, page_size: int = 20):
        actor = auth.session_user(request)
        auth.require(actor, "users.read")
        page_size, page = min(max(page_size, 1), 100), max(page, 1)
        condition = or_(
            users.c.display_name.contains(q, autoescape=True),
            users.c.phone.contains(q, autoescape=True),
            users.c.email.contains(q, autoescape=True),
        )
        with transaction() as c:
            total = c.execute(select(func.count()).select_from(users).where(condition)).scalar_one()
            data = [
                dict(r)
                for r in c.execute(
                    select(
                        users.c.id,
                        users.c.display_name,
                        users.c.phone,
                        users.c.email,
                        users.c.roles,
                        users.c.status,
                        users.c.version,
                        users.c.created_at,
                    )
                    .where(condition)
                    .order_by(users.c.created_at)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                ).mappings()
            ]
        return {"users": data, "total": total, "page": page, "page_size": page_size}

    @app.put("/api/v1/admin/users/{user_id}/roles")
    def role_put(user_id: str, body: Roles, request: Request):
        return auth.manage_user(
            auth.session_user(request),
            user_id,
            roles=body.roles,
            expected_version=body.expected_version,
        )

    @app.patch("/api/v1/admin/users/{user_id}/status")
    def status_put(user_id: str, body: Status, request: Request):
        return auth.manage_user(
            auth.session_user(request),
            user_id,
            status=body.status,
            expected_version=body.expected_version,
        )

    @app.get("/api/v1/admin/workspaces")
    def admin_workspaces(request: Request, page: int = 1):
        actor = auth.session_user(request)
        auth.require(actor, "workspace.supervise")
        with transaction() as c:
            return {
                "workspaces": [
                    dict(r)
                    for r in c.execute(
                        select(workspaces)
                        .order_by(workspaces.c.created_at)
                        .offset((max(page, 1) - 1) * 100)
                        .limit(100)
                    ).mappings()
                ]
            }

    @app.post("/api/v1/admin/legacy-workspace-bindings")
    def legacy_bind(body: Bind, request: Request):
        actor = auth.session_user(request)
        auth.require(actor, "legacy.bind")
        with transaction() as c:
            c.execute(
                select(locks).where(locks.c.name == "admin_membership").with_for_update()
            ).one()
            fresh = (
                c.execute(select(users).where(users.c.id == actor["id"]).with_for_update())
                .mappings()
                .one()
            )
            if fresh["status"] != "active" or fresh["auth_version"] != actor["auth_version"]:
                auth.fail(403, "PERMISSION_DENIED", "权限已变化，请重新登录。")
            auth.require(fresh, "legacy.bind")
            repo.workspace_access(c, actor, body.workspace_id)
            ws = (
                c.execute(
                    select(workspaces).where(workspaces.c.id == body.workspace_id).with_for_update()
                )
                .mappings()
                .one()
            )
            if ws["state"] != "unclaimed_legacy":
                auth.fail(409, "ALREADY_BOUND", "此空间已绑定用户。")
            if not c.execute(
                select(users.c.id).where(users.c.id == body.user_id, users.c.status == "active")
            ).first():
                auth.fail(404, "NOT_FOUND", "目标用户不存在或不可用。")
            c.execute(
                update(workspaces)
                .where(
                    workspaces.c.id == body.workspace_id, workspaces.c.state == "unclaimed_legacy"
                )
                .values(owner_user_id=body.user_id, state="active")
            )
            auth.audit_event(
                c, actor["id"], "legacy.bound", body.workspace_id, {"user_id": body.user_id}
            )
        return {"ok": True}

    from .voice import router as voice_router

    app.include_router(voice_router)

    # Legacy demo/token/role endpoints are deliberately unmounted. Unknown API != SPA HTML.
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown_api(rest: str):
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "接口不存在。"})

    frontend = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if frontend.exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/{rest:path}", include_in_schema=False)
        def ui(rest: str):
            public_files = {
                "voice-worklet.js": "text/javascript",
                "favicon.svg": "image/svg+xml",
                "favicon.ico": "image/x-icon",
                "apple-touch-icon.png": "image/png",
            }
            if rest in public_files and (frontend / rest).exists():
                return FileResponse(frontend / rest, media_type=public_files[rest])
            return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-store"})

    return app


app = create_app()
