"""Signed local demo identities and short-lived audio links."""

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Header, HTTPException

SECRET = secrets.token_bytes(32)


def sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
    return body + "." + signature


def verify(token: str) -> dict:
    try:
        body, signature = token.split(".")
        expected = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload.get("expires", 0) < time.time():
            raise ValueError
        return payload
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, "会话身份已过期，请重新进入。") from None


def identity(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ")
    payload = verify(token)
    if payload.get("role") not in ["talker", "hinter"]:
        raise HTTPException(401, "无效身份。")
    return payload


def require(actor: dict, role: str) -> None:
    if actor["role"] != role:
        raise HTTPException(403, "当前角色不能执行该操作。")
