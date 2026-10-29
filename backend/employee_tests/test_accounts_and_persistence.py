"""Integration acceptance against the dedicated local MySQL (no SQLite mocks)."""

import asyncio
import secrets

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import insert, select, update

from app.employee import auth
from app.employee.checkpoint import MySQLSaver, MySQLStore
from app.employee.db import now, transaction, uid, users
from app.employee.web import app


@pytest.fixture(scope="session", autouse=True)
def initialize():
    import os

    assert os.getenv("MYSQL_DATABASE") == "duet_acceptance", "Use the dedicated acceptance database"
    from app.employee.migrate import migrate

    migrate()
    from sqlalchemy import delete

    from app.employee.db import audit, auth_limits

    with transaction() as c:
        c.execute(delete(auth_limits))
        c.execute(
            insert(audit).values(
                id=uid("testhealth"),
                action="worker.ready",
                payload={"fixture": True},
                created_at=now(),
            )
        )


@pytest.fixture
def account():
    c = TestClient(app)
    digits = str(secrets.randbelow(10000000)).zfill(7)
    body = {
        "phone": "+86138" + digits + "1",
        "email": f"accept-{secrets.token_hex(6)}@example.com",
        "password": "Aa9!" + secrets.token_urlsafe(20),
        "display_name": "验收用户" + digits,
    }
    r = c.post("/api/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    login = c.post(
        "/api/v1/auth/login", json={"identifier": body["email"], "password": body["password"]}
    )
    assert login.status_code == 200
    profile = login.json()
    c.headers["X-CSRF-Token"] = profile["csrf_token"]
    yield c, body, profile
    from app.employee.db import runs

    with transaction() as db:
        db.execute(
            update(runs)
            .where(runs.c.owner_user_id == profile["user"]["id"])
            .values(status="cancelled", cancel_requested=True)
        )


def test_registration_phone_email_password_and_session(account):
    c, body, profile = account
    assert profile["roles"] == ["talker"]
    assert "hinter_admin" not in profile["roles"]
    assert c.post("/api/v1/auth/register", json=body).status_code == 409
    assert c.post("/api/v1/auth/register", json={**body, "is_admin": True}).status_code == 422
    assert c.get("/api/v1/admin/users").status_code == 403
    assert c.post("/api/v1/auth/demo", json={"role": "hinter"}).status_code == 404
    c.post("/api/v1/auth/logout", json={})
    assert c.get("/api/v1/auth/me").status_code == 401
    assert (
        c.post(
            "/api/v1/auth/login",
            json={"identifier": body["phone"].removeprefix("+86"), "password": body["password"]},
        ).status_code
        == 200
    )


def test_csrf_origin_and_idempotency(account):
    c, _, profile = account
    path = "/api/v1/threads"
    assert (
        c.post(
            path,
            json={"workspace_id": profile["default_workspace_id"]},
            headers={"X-CSRF-Token": "bad"},
        ).status_code
        == 403
    )
    assert (
        c.post(
            path,
            json={"workspace_id": profile["default_workspace_id"]},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    t = c.post(path, json={"workspace_id": profile["default_workspace_id"]}).json()
    path = f"/api/v1/threads/{t['id']}/inputs"
    h = {"Idempotency-Key": secrets.token_hex(8)}
    body = {"input": {"text": "记录：试点周五结束。"}}
    a, b = c.post(path, json=body, headers=h), c.post(path, json=body, headers=h)
    assert a.status_code == 202 and a.json() == b.json()
    assert c.post(path, json={"input": {"text": "不同的内容"}}, headers=h).status_code == 409
    assert (
        c.post(
            path,
            json={**body, "presentation_mode": "summary"},
            headers={"Idempotency-Key": "future-mode"},
        ).status_code
        == 422
    )


def test_cross_user_threads_memory_runs_and_url_forgery(account):
    c, _, profile = account
    t = c.post("/api/v1/threads", json={"workspace_id": profile["default_workspace_id"]}).json()
    r = c.post(
        f"/api/v1/threads/{t['id']}/inputs",
        json={"input": {"text": "隔离检查"}},
        headers={"Idempotency-Key": "isolation"},
    ).json()
    other = TestClient(app)
    with transaction() as db:
        actor_id, _ = auth.create_user(
            db,
            phone_value=None,
            email_value="other-" + secrets.token_hex(6) + "@example.com",
            display_name="隔离账号",
            password_hash=auth.password("OtherSecurePassword123!"),
        )
        email = db.execute(select(users.c.email).where(users.c.id == actor_id)).scalar_one()
    login = other.post(
        "/api/v1/auth/login", json={"identifier": email, "password": "OtherSecurePassword123!"}
    ).json()
    other.headers["X-CSRF-Token"] = login["csrf_token"]
    for path in (
        f"/threads/{t['id']}",
        f"/threads/{t['id']}/records",
        f"/threads/{t['id']}/projections",
        f"/runs/{r['run_id']}/events",
        f"/workspaces/{profile['default_workspace_id']}/memory",
    ):
        assert other.get("/api/v1" + path + "?role=hinter").status_code == 404
    assert other.post("/api/v1/runs/" + r["run_id"] + "/cancel", json={}).status_code == 404


def test_admin_force_change_role_revoke_and_private_notes(account):
    c, body, profile = account
    password = "InitialAdminPassword123!"
    with transaction() as db:
        admin_id, ws = auth.create_user(
            db,
            display_name="验收管理员",
            password_hash=auth.password(password),
            alias="accept-" + secrets.token_hex(6),
            admin=True,
        )
        alias = db.execute(select(users.c.login_alias).where(users.c.id == admin_id)).scalar_one()
    admin = TestClient(app)
    first = admin.post(
        "/api/v1/auth/login", json={"identifier": alias, "password": password}
    ).json()
    admin.headers["X-CSRF-Token"] = first["csrf_token"]
    assert first["must_change_password"]
    assert admin.get("/api/v1/admin/users").status_code == 403
    changed = admin.post(
        "/api/v1/auth/change-password",
        json={"old_password": password, "new_password": "ChangedAdminPassword123!"},
    ).json()
    admin.headers["X-CSRF-Token"] = changed["csrf_token"]
    assert not changed["must_change_password"]
    current = c.get("/api/v1/auth/me").json()
    grant = admin.put(
        "/api/v1/admin/users/" + profile["user"]["id"] + "/roles",
        json={"roles": ["talker", "hinter_admin"], "expected_version": current["user"]["version"]},
    )
    assert grant.status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401
    login = c.post(
        "/api/v1/auth/login", json={"identifier": body["email"], "password": body["password"]}
    ).json()
    assert "roles.assign" in login["capabilities"]
    c.headers["X-CSRF-Token"] = login["csrf_token"]
    assert (
        admin.put(
            "/api/v1/admin/users/" + profile["user"]["id"] + "/roles",
            json={"roles": ["talker"], "expected_version": grant.json()["version"]},
        ).status_code
        == 200
    )
    assert c.get("/api/v1/auth/me").status_code == 401
    login = c.post(
        "/api/v1/auth/login", json={"identifier": body["email"], "password": body["password"]}
    ).json()
    c.headers["X-CSRF-Token"] = login["csrf_token"]
    t = c.post("/api/v1/threads", json={"workspace_id": profile["default_workspace_id"]}).json()
    note = admin.post(
        "/api/v1/threads/" + t["id"] + "/guidance",
        json={"text": "private-canary-never-show", "visibility": "admin_private"},
    )
    assert note.status_code == 201
    assert "private-canary-never-show" not in c.get("/api/v1/threads/" + t["id"] + "/records").text
    assert "private-canary-never-show" in admin.get("/api/v1/threads/" + t["id"] + "/records").text


def test_mysql_native_checkpoint_and_store_survive_new_instances():
    key = uid("checkpoint_test")
    cfg = {"configurable": {"thread_id": key, "checkpoint_ns": "nested:scope"}}
    saver = MySQLSaver()
    cp = empty_checkpoint()
    cp["channel_values"] = {"messages": [HumanMessage("persisted input")]}
    cp["channel_versions"] = {"messages": 1}
    saved = saver.put(cfg, cp, {"source": "input", "step": 0, "parents": {}}, {"messages": 1})
    saver.put_writes(saved, [("messages", [HumanMessage("pending output")])], "native-task")
    restored = MySQLSaver().get_tuple(saved)
    assert restored.checkpoint["channel_values"]["messages"][0].content == "persisted input"
    assert restored.pending_writes[0][0] == "native-task"

    async def check():
        assert (await MySQLSaver().aget_tuple(saved)).checkpoint["id"] == cp["id"]

    asyncio.run(check())
    store = MySQLStore()
    store.put(("accept", key), "fact", {"content": "durable"})
    assert MySQLStore().get(("accept", key), "fact").value["content"] == "durable"
    store.delete(("accept", key), "fact")
    saver.delete_thread(key)


def test_last_admin_concurrent_guard_in_database():
    from concurrent.futures import ThreadPoolExecutor

    from fastapi import HTTPException

    # This database is dedicated to acceptance; isolate the administrator set.
    with transaction() as c:
        snapshots = [
            dict(u) for u in c.execute(select(users)).mappings() if "hinter_admin" in u["roles"]
        ]
        for user in snapshots:
            c.execute(update(users).where(users.c.id == user["id"]).values(roles=["talker"]))
        first, _ = auth.create_user(
            c,
            display_name="并发管理员一",
            password_hash=auth.password("ConcurrencyAdmin123!"),
            alias=uid("testadmin"),
            admin=True,
        )
        second, _ = auth.create_user(
            c,
            display_name="并发管理员二",
            password_hash=auth.password("ConcurrencyAdmin123!"),
            alias=uid("testadmin"),
            admin=True,
        )
        admins = [
            dict(u)
            for u in c.execute(select(users).where(users.c.id.in_([first, second]))).mappings()
        ]

    def revoke(user):
        try:
            auth.manage_user(user, user["id"], roles=["talker"], expected_version=user["version"])
            return "revoked"
        except HTTPException as e:
            return e.detail["code"]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(revoke, admins))
        assert sorted(results) == ["LAST_ADMIN_REQUIRED", "revoked"]
    finally:
        with transaction() as c:
            for user in snapshots:
                c.execute(update(users).where(users.c.id == user["id"]).values(roles=user["roles"]))


def test_long_unicode_memory_and_revision(account):
    c, _, profile = account
    body = {"name": "长会议记录", "text": "会议决定需保存。" * 6000, "visibility": "user_shared"}
    assert len(body["text"]) <= 60000
    r = c.put("/api/v1/workspaces/" + profile["default_workspace_id"] + "/memory", json=body)
    assert r.status_code == 200
    assert (
        c.put(
            "/api/v1/workspaces/" + profile["default_workspace_id"] + "/memory",
            json={**body, "text": "new", "expected_revision": 0},
        ).status_code
        == 409
    )
    stored = c.get("/api/v1/workspaces/" + profile["default_workspace_id"] + "/memory").json()[
        "documents"
    ]
    assert stored[0]["text"] == body["text"]


def test_native_tool_write_idempotency_and_background_checkpoint_fencing(account):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from app.employee.agent_tools import recall_memory, remember
    from app.employee.db import memories, runs

    c, _, profile = account
    t = c.post("/api/v1/threads", json={"workspace_id": profile["default_workspace_id"]}).json()
    accepted = c.post(
        "/api/v1/threads/" + t["id"] + "/inputs",
        json={"input": {"text": "保存验收事实"}},
        headers={"Idempotency-Key": "fence"},
    ).json()
    with transaction() as db:
        db.execute(
            update(runs)
            .where(runs.c.id == accepted["run_id"])
            .values(
                status="running", attempt=1, lease_token="test-lease-owner", lease_until=now() + 60
            )
        )
        run = dict(db.execute(select(runs).where(runs.c.id == accepted["run_id"])).mappings().one())
    runtime = SimpleNamespace(
        context={
            "user_id": profile["user"]["id"],
            "authz_attributes": {"app_run_id": run["id"], "lease_token": run["lease_token"]},
        },
        tool_call_id="native-call-durable",
    )
    first = remember.func(runtime=runtime, name="验收事实", content="测试代号青鸟")
    import json

    assert json.loads(
        remember.func(runtime=runtime, name="验收事实", content="测试代号青鸟")
    ) == json.loads(first)
    with transaction() as db:
        assert (
            db.execute(
                select(memories.c.revision).where(
                    memories.c.workspace_id == profile["default_workspace_id"]
                )
            ).scalar_one()
            == 1
        )
    assert "青鸟" in recall_memory.func(runtime=runtime, query="青鸟")
    saver = MySQLSaver(require_grant=True)
    saver.bind_run(run)
    cfg = {
        "configurable": {"thread_id": t["id"], "checkpoint_ns": "", "duet_app_run_id": run["id"]}
    }
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": [HumanMessage("durable")]}
    with ThreadPoolExecutor(max_workers=1) as pool:
        saved = pool.submit(
            saver.put, cfg, checkpoint, {"source": "loop", "step": 1, "parents": {}}, {}
        ).result()
    assert MySQLSaver().get_tuple(saved).checkpoint["id"] == checkpoint["id"]
    with transaction() as db:
        db.execute(
            update(runs).where(runs.c.id == run["id"]).values(lease_token="replacement-worker")
        )
    with pytest.raises(PermissionError):
        remember.func(runtime=runtime, name="验收事实", content="测试代号青鸟")
    with pytest.raises(PermissionError):
        saver.put(cfg, empty_checkpoint(), {"source": "loop", "step": 2, "parents": {}}, {})


def test_withdrawn_audio_is_not_downloadable(account):
    from app.employee.db import records
    from app.employee.voice import publish_file

    c, _, profile = account
    t = c.post("/api/v1/threads", json={"workspace_id": profile["default_workspace_id"]}).json()
    accepted = c.post(
        "/api/v1/threads/" + t["id"] + "/inputs",
        json={"input": {"text": "音频归属验收"}},
        headers={"Idempotency-Key": "audio-owner"},
    ).json()
    artifact = publish_file(
        t["id"], b"validation-audio", "audio/wav", ".wav", accepted["record_id"]
    )
    assert c.get("/api/v1/artifacts/" + artifact).status_code == 200
    with transaction() as db:
        db.execute(
            update(records).where(records.c.id == accepted["record_id"]).values(withdrawn=True)
        )
    assert c.get("/api/v1/artifacts/" + artifact).status_code == 404


def test_main_completion_queues_independent_projection(account):
    from app.employee import worker
    from app.employee.db import projections, records, runs

    client, _, profile = account
    thread = client.post(
        "/api/v1/threads", json={"workspace_id": profile["default_workspace_id"]}
    ).json()
    accepted = client.post(
        f"/api/v1/threads/{thread['id']}/inputs",
        json={"input": {"text": "会议下周几结束？"}},
        headers={"Idempotency-Key": "split-projection"},
    ).json()
    with transaction() as c:
        c.execute(
            update(runs)
            .where(runs.c.id == accepted["run_id"])
            .values(status="running", lease_token="test-lease", lease_until=now() + 60)
        )
        run = dict(c.execute(select(runs).where(runs.c.id == accepted["run_id"])).mappings().one())
    worker.complete(run, "completed", "会议暂定周五结束。")
    with transaction() as c:
        main = c.execute(select(runs).where(runs.c.id == run["id"])).mappings().one()
        child = c.execute(select(runs).where(runs.c.parent_run_id == run["id"])).mappings().one()
        output = (
            c.execute(
                select(records).where(
                    records.c.thread_id == thread["id"], records.c.role == "assistant"
                )
            )
            .mappings()
            .one()
        )
        projected = (
            c.execute(select(projections).where(projections.c.record_id == run["record_id"]))
            .mappings()
            .one()
        )
    assert main["status"] == "completed" and output["text"] == "会议暂定周五结束。"
    assert child["kind"] == "projection" and child["status"] == "queued"
    assert projected["status"] == "pending"


def test_voice_origin_confirmed_input_interrupt_and_disconnect(account, monkeypatch):
    import array

    from starlette.websockets import WebSocketDisconnect

    from app.employee import voice
    from app.employee.db import records, runs

    client, _, profile = account
    thread = client.post(
        "/api/v1/threads", json={"workspace_id": profile["default_workspace_id"]}
    ).json()
    endpoint = f"/api/v1/threads/{thread['id']}/voice"
    for origin in (None, "https://evil.example"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(endpoint, headers={"Origin": origin} if origin else {}):
                pass
    with client.websocket_connect(endpoint, headers={"Origin": "http://testserver"}) as socket:
        socket.send_json(
            {
                "type": "input.confirmed",
                "text": "语音确认记录",
                "idempotency_key": "voice-confirmed",
            }
        )
        accepted = socket.receive_json()
        assert accepted["type"] == "input.accepted"
        socket.send_json({"type": "audio.interrupt"})
        assert socket.receive_json() == {"type": "audio.interrupted", "runs_cancelled": False}
    monkeypatch.setattr(voice, "transcribe_bytes", lambda _: "挂断前最后一句也会保存。")
    with client.websocket_connect(endpoint, headers={"Origin": "http://testserver"}) as socket:
        socket.send_bytes(array.array("h", [4000] * 8192).tobytes())
        socket.send_json({"type": "ping"})
        assert socket.receive_json()["type"] == "pong"
    with transaction() as c:
        saved = (
            c.execute(select(records.c.text).where(records.c.thread_id == thread["id"]))
            .scalars()
            .all()
        )
        status = c.execute(
            select(runs.c.status).where(runs.c.id == accepted["run_id"])
        ).scalar_one()
    assert "挂断前最后一句也会保存。" in saved
    assert status == "queued"


def test_queue_cancellation_record_revision_and_event_cursor(account):
    from app.employee.db import records, revisions

    client, _, profile = account
    thread = client.post(
        "/api/v1/threads", json={"workspace_id": profile["default_workspace_id"]}
    ).json()

    def submit(text, key):
        return client.post(
            f"/api/v1/threads/{thread['id']}/inputs",
            json={"input": {"kind": "transcript", "text": text}},
            headers={"Idempotency-Key": key},
        ).json()

    first, second = (
        submit("会议在周四。", "queue-first"),
        submit("请继续记录预算。", "queue-second"),
    )
    corrected = client.patch(
        "/api/v1/records/" + first["record_id"],
        json={"text": "会议在周五。", "expected_revision": 1},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["revision"] == 2
    with transaction() as c:
        assert (
            c.execute(
                select(revisions.c.text).where(
                    revisions.c.record_id == first["record_id"], revisions.c.revision == 1
                )
            ).scalar_one()
            == "会议在周四。"
        )
        assert (
            c.execute(select(records.c.text).where(records.c.id == first["record_id"])).scalar_one()
            == "会议在周五。"
        )
    assert client.post("/api/v1/runs/" + first["run_id"] + "/cancel", json={}).status_code == 200
    state = client.get("/api/v1/threads/" + thread["id"]).json()["runs"]
    assert next(r for r in state if r["id"] == second["run_id"])["status"] == "queued"
    assert (
        client.get("/api/v1/runs/" + second["run_id"] + "/events?after_seq=1").json()["events"]
        == []
    )
    assert (
        client.get("/api/v1/runs/" + second["run_id"] + "/events?after_seq=0").json()["events"][0][
            "seq"
        ]
        == 1
    )
    assert (
        client.get("/api/v1/threads/" + thread["id"] + "/projections").json()["projections"][0][
            "source_revision"
        ]
        == 2
    )


def test_voice_connection_rechecks_revoked_session(account):
    from starlette.websockets import WebSocketDisconnect

    client, _, profile = account
    thread = client.post(
        "/api/v1/threads", json={"workspace_id": profile["default_workspace_id"]}
    ).json()
    with client.websocket_connect(
        f"/api/v1/threads/{thread['id']}/voice", headers={"Origin": "http://testserver"}
    ) as socket:
        with transaction() as c:
            c.execute(
                update(users)
                .where(users.c.id == profile["user"]["id"])
                .values(auth_version=users.c.auth_version + 1)
            )
        socket.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_unclaimed_legacy_requires_explicit_admin_binding(account):
    from app.employee.db import workspaces

    client, body, profile = account
    legacy = uid("legacyworkspace")
    with transaction() as c:
        c.execute(
            insert(workspaces).values(
                id=legacy,
                owner_user_id=None,
                name="同名旧空间",
                state="unclaimed_legacy",
                created_at=now(),
            )
        )
    assert client.get("/api/v1/workspaces/" + legacy + "/memory").status_code == 404
    binding = {"workspace_id": legacy, "user_id": profile["user"]["id"]}
    assert client.post("/api/v1/admin/legacy-workspace-bindings", json=binding).status_code == 403
    with transaction() as c:
        c.execute(
            update(users)
            .where(users.c.id == profile["user"]["id"])
            .values(roles=["talker", "hinter_admin"], auth_version=users.c.auth_version + 1)
        )
    login = client.post(
        "/api/v1/auth/login", json={"identifier": body["email"], "password": body["password"]}
    ).json()
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    assert client.post("/api/v1/threads", json={"workspace_id": legacy}).status_code == 409
    assert client.post("/api/v1/admin/legacy-workspace-bindings", json=binding).status_code == 200
    assert client.post("/api/v1/admin/legacy-workspace-bindings", json=binding).status_code == 409
    with transaction() as c:
        c.execute(
            update(users)
            .where(users.c.id == profile["user"]["id"])
            .values(roles=["talker"], auth_version=users.c.auth_version + 1)
        )
    login = client.post(
        "/api/v1/auth/login", json={"identifier": body["email"], "password": body["password"]}
    ).json()
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    assert client.get("/api/v1/workspaces/" + legacy + "/memory").status_code == 200
    assert client.post("/api/v1/threads", json={"workspace_id": legacy}).status_code == 201


def test_future_schema_is_rejected_before_startup():
    from sqlalchemy import delete

    from app.employee.db import schema
    from app.employee.migrate import migrate

    with transaction() as c:
        c.execute(insert(schema).values(version=999, applied_at=now()))
    try:
        with pytest.raises(RuntimeError):
            migrate()
    finally:
        with transaction() as c:
            c.execute(delete(schema).where(schema.c.version == 999))
