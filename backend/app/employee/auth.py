"""Server sessions, CSRF and transactional role management."""

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

import phonenumbers
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException, Request, Response
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert

from .db import audit, auth_limits, auth_sessions, locks, now, transaction, uid, users, workspaces

hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))
COOKIE = "duet_session"
BASE_CAPS = ["chat.use", "records.read", "memory.manage"]
ADMIN_CAPS = [
    "workspace.supervise",
    "guidance.write",
    "users.read",
    "users.manage_status",
    "roles.assign",
    "legacy.bind",
]


def fail(status, code, message):
    raise HTTPException(status, {"code": code, "message": message})


def phone(value):
    try:
        parsed = phonenumbers.parse(value.strip(), None)
        if not value.strip().startswith("+") or not phonenumbers.is_valid_number(parsed):
            raise ValueError()
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except (ValueError, phonenumbers.NumberParseException):
        fail(422, "VALIDATION_ERROR", "请输入包含国家区号的有效手机号，例如 +8613800000000。")


def email(value):
    normalized = value.strip().casefold()
    if len(normalized) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
        fail(422, "VALIDATION_ERROR", "请输入有效邮箱。")
    return normalized


def password(value):
    if len(value) < 12 or len(value) > 256:
        fail(422, "VALIDATION_ERROR", "密码需要 12–256 个字符。")
    return hasher.hash(value)


def check_password(encoded, value):
    try:
        return hasher.verify(encoded, value)
    except VerificationError:
        return False


def audit_event(c, actor, action, target, payload=None):
    c.execute(
        insert(audit).values(
            id=uid("audit"),
            actor_user_id=actor,
            action=action,
            target_id=target,
            payload=payload or {},
            created_at=now(),
        )
    )


def rate_limit(request, operation, identifier=""):
    # Shared MySQL counters prevent bypass across web processes; never store raw login identifiers.
    remote = request.client.host if request.client else "unknown"
    window = int(time.time() // 900)
    keys = [hashlib.sha256(f"{operation}:ip:{remote}".encode()).hexdigest()]
    if identifier:
        keys.append(
            hashlib.sha256(f"{operation}:account:{identifier.casefold()}".encode()).hexdigest()
        )
    limited = False
    with transaction() as c:
        for key in keys:
            c.execute(
                mysql_insert(auth_limits)
                .values(key=key, window=window, count=0)
                .on_duplicate_key_update(key=key)
            )
            r = (
                c.execute(select(auth_limits).where(auth_limits.c.key == key).with_for_update())
                .mappings()
                .one()
            )
            count = r["count"] + 1 if r["window"] == window else 1
            c.execute(
                update(auth_limits)
                .where(auth_limits.c.key == key)
                .values(window=window, count=count)
            )
            limited |= count > (100 if len(keys) == 1 else 30)
    if limited:
        fail(429, "AUTH_RATE_LIMITED", "尝试次数过多，请 15 分钟后再试。")


def origin_check(request):
    origin = request.headers.get("origin")
    expected = os.getenv("APP_ORIGIN", str(request.base_url).rstrip("/"))
    if origin and origin.rstrip("/") != expected:
        fail(403, "CSRF_FAILED", "请求来源无效，请刷新页面。")
    if request.headers.get("sec-fetch-site") == "cross-site":
        fail(403, "CSRF_FAILED", "请求来源无效。")


def public_mutation(request):
    origin_check(request)
    if "application/json" not in request.headers.get("content-type", ""):
        fail(415, "JSON_REQUIRED", "请使用 JSON 请求。")


def session_user(request: Request, *, allow_password_change=False):
    raw = request.cookies.get(COOKIE, "")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with transaction() as c:
        session = (
            c.execute(select(auth_sessions).where(auth_sessions.c.digest == digest))
            .mappings()
            .first()
        )
        user = (
            c.execute(select(users).where(users.c.id == session["user_id"])).mappings().first()
            if session
            else None
        )
    if (
        not user
        or session["expires_at"] <= now()
        or user["status"] != "active"
        or session["auth_version"] != user["auth_version"]
    ):
        fail(401, "SESSION_EXPIRED", "请登录后继续，未发送内容会保留在当前页面。")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin_check(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            fail(403, "CSRF_FAILED", "验证已过期，请刷新页面。")
    if user["must_change_password"] and not allow_password_change:
        fail(403, "PASSWORD_CHANGE_REQUIRED", "请先修改初始密码。")
    return {**dict(user), "session_digest": digest, "csrf_token": session["csrf"]}


def require(user, capability):
    if capability not in capabilities(user):
        fail(403, "PERMISSION_DENIED", "当前账号没有此操作权限。")


def capabilities(user):
    return BASE_CAPS + (ADMIN_CAPS if "hinter_admin" in user["roles"] else [])


def describe(user):
    with transaction() as c:
        workspace = c.execute(
            select(workspaces.c.id)
            .where(workspaces.c.owner_user_id == user["id"], workspaces.c.state == "active")
            .order_by(workspaces.c.created_at)
        ).scalar()
    return {
        "user": {k: user[k] for k in ("id", "display_name", "phone", "email", "version", "status")},
        "roles": user["roles"],
        "capabilities": capabilities(user),
        "must_change_password": user["must_change_password"],
        "phone_verified": False,
        "email_verified": False,
        "default_workspace_id": workspace,
        "csrf_token": user.get("csrf_token"),
    }


def establish(c, user, response: Response):
    raw = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    ttl = int(os.getenv("SESSION_TTL_SECONDS", "604800"))
    c.execute(
        insert(auth_sessions).values(
            digest=hashlib.sha256(raw.encode()).hexdigest(),
            user_id=user["id"],
            csrf=csrf,
            auth_version=user["auth_version"],
            expires_at=now() + ttl,
        )
    )
    response.set_cookie(
        COOKIE,
        raw,
        max_age=ttl,
        httponly=True,
        secure=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        path="/",
    )
    return {**dict(user), "csrf_token": csrf}


def create_user(
    c,
    *,
    phone_value=None,
    email_value=None,
    display_name=None,
    password_hash,
    alias=None,
    admin=False,
):
    user_id, workspace_id = uid("user"), uid("workspace")
    c.execute(
        insert(users).values(
            id=user_id,
            phone=phone_value,
            email=email_value,
            login_alias=alias,
            display_name=display_name or f"用户 {phone_value[-4:]}",
            password_hash=password_hash,
            roles=["talker", "hinter_admin"] if admin else ["talker"],
            status="active",
            version=1,
            auth_version=1,
            must_change_password=admin,
            created_at=now(),
        )
    )
    c.execute(
        insert(workspaces).values(
            id=workspace_id,
            owner_user_id=user_id,
            name=f"{display_name or '我的'}的工作空间",
            state="active",
            created_at=now(),
        )
    )
    return user_id, workspace_id


def bootstrap():
    with transaction() as c:
        c.execute(select(locks).where(locks.c.name == "admin_membership").with_for_update()).one()
        # Presence of audit marker, not active admin role, controls one-time bootstrap.
        if c.execute(select(audit.c.id).where(audit.c.action == "bootstrap.created")).first():
            return False
        source = Path(
            os.getenv("BOOTSTRAP_ADMIN_FILE", ".runtime/bootstrap/bootstrap-admin.json")
        )
        if not source.is_file():
            raise RuntimeError(
                "Initial administrator login and password must be set during deployment."
            )

        config = json.loads(source.read_text(encoding="utf-8"))
        alias, initial = config.get("login"), config.get("password")
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{2,63}", alias):
            raise ValueError("Invalid initial administrator login")
        if not isinstance(initial, str):
            raise ValueError("Initial administrator password is required")
        user_id, _ = create_user(
            c,
            phone_value=phone(os.environ["BOOTSTRAP_ADMIN_PHONE"])
            if os.getenv("BOOTSTRAP_ADMIN_PHONE")
            else None,
            email_value=email(os.environ["BOOTSTRAP_ADMIN_EMAIL"])
            if os.getenv("BOOTSTRAP_ADMIN_EMAIL")
            else None,
            display_name="Hinter Admin",
            password_hash=password(initial),
            alias=alias,
            admin=True,
        )
        audit_event(c, user_id, "bootstrap.created", user_id)
    return True


def manage_user(actor, target_id, *, expected_version, roles=None, status=None):
    require(actor, "roles.assign" if roles is not None else "users.manage_status")
    if roles is not None and (set(roles) - {"talker", "hinter_admin"} or "talker" not in roles):
        fail(422, "VALIDATION_ERROR", "角色必须包含 talker，可选 hinter_admin。")
    if status is not None and status not in ("active", "disabled"):
        fail(422, "VALIDATION_ERROR", "账号状态无效。")
    with transaction() as c:
        c.execute(select(locks).where(locks.c.name == "admin_membership").with_for_update()).one()
        fresh_actor = (
            c.execute(select(users).where(users.c.id == actor["id"]).with_for_update())
            .mappings()
            .one()
        )
        if (
            fresh_actor["status"] != "active"
            or fresh_actor["auth_version"] != actor["auth_version"]
        ):
            fail(403, "PERMISSION_DENIED", "权限已变化，请刷新页面。")
        require(fresh_actor, "roles.assign" if roles is not None else "users.manage_status")
        target = (
            c.execute(select(users).where(users.c.id == target_id).with_for_update())
            .mappings()
            .first()
        )
        if not target:
            fail(404, "NOT_FOUND", "用户不存在。")
        if target["version"] != expected_version:
            fail(409, "VERSION_CONFLICT", "用户资料已变化，请刷新后重试。")
        next_roles, next_status = (
            roles if roles is not None else target["roles"],
            status or target["status"],
        )
        admins = [
            u
            for u in c.execute(select(users).where(users.c.status == "active")).mappings()
            if "hinter_admin" in u["roles"]
        ]
        if (
            target["status"] == "active"
            and "hinter_admin" in target["roles"]
            and (next_status != "active" or "hinter_admin" not in next_roles)
            and len(admins) <= 1
        ):
            fail(409, "LAST_ADMIN_REQUIRED", "必须至少保留一位可用的 Hinter 管理员。")
        c.execute(
            update(users)
            .where(users.c.id == target_id)
            .values(
                roles=next_roles,
                status=next_status,
                version=target["version"] + 1,
                auth_version=target["auth_version"] + 1,
            )
        )
        c.execute(delete(auth_sessions).where(auth_sessions.c.user_id == target_id))
        audit_event(
            c,
            actor["id"],
            "user.roles" if roles is not None else "user.status",
            target_id,
            {"roles": next_roles, "status": next_status},
        )
    return {
        "id": target_id,
        "roles": next_roles,
        "status": next_status,
        "version": expected_version + 1,
    }
