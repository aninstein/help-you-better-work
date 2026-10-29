"""Operator-controlled tools; every execution rechecks the original actor's grant."""

import asyncio
import hashlib
import ipaddress
import json
import os
import socket
from contextlib import nullcontext
from contextvars import ContextVar
from datetime import datetime
from typing import Literal
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from langchain.tools import ToolRuntime, tool
from sqlalchemy import insert, or_, select

from .db import commands, now, records, runs, threads, transaction, users, workspaces
from .repository import put_memory

ACTIVE_RUN = ContextVar("duet_active_run", default=None)

SAFE_TOOLS = {
    "current_time",
    "recall_records",
    "recall_memory",
    "remember",
    "web_search",
    "web_fetch",
    "describe_skill",
    "task",
    "inspect_run_status",
    "request_hinter",
}


def current_grant(context, connection=None, *, lock=False):
    attrs = context.get("authz_attributes") or {}
    app_run_id = attrs.get("app_run_id")
    with nullcontext(connection) if connection is not None else transaction() as c:
        query = select(runs).where(runs.c.id == app_run_id)
        if lock:
            query = query.with_for_update()
        run = c.execute(query).mappings().first()
        actor = (
            c.execute(select(users).where(users.c.id == run["actor_user_id"])).mappings().first()
            if run
            else None
        )
        if (
            not run
            or not actor
            or run["lease_token"] != attrs.get("lease_token")
            or run["cancel_requested"]
            or run["status"] != "running"
            or not run["lease_until"]
            or run["lease_until"] < now()
            or actor["auth_version"] != run["auth_version"]
            or actor["status"] != "active"
        ):
            raise PermissionError("Run authorization expired")
        if context.get("user_id") != run["owner_user_id"]:
            raise PermissionError("Run owner mismatch")
        if actor["id"] != run["owner_user_id"] and "hinter_admin" not in actor["roles"]:
            raise PermissionError("Supervisor permission revoked")
        thread = c.execute(select(threads).where(threads.c.id == run["thread_id"])).mappings().one()
        from .answer_policy import ensure_run_allowed

        ensure_run_allowed(c, run)
        return dict(run), dict(actor), dict(thread)


@tool
def current_time(runtime: ToolRuntime, timezone: str = "Asia/Shanghai") -> str:
    """Read the real current date and time in an IANA timezone."""
    current_grant(runtime.context)
    return datetime.now(ZoneInfo(timezone)).isoformat()


@tool
def request_hinter(
    runtime: ToolRuntime,
    summary: str,
    needed_information: str,
    reason: Literal["internal_data", "uncertain_facts", "disclosure_needed"] = "uncertain_facts",
) -> str:
    """Ask Hinter for missing internal facts, uncertainty resolution or permitted disclosure.

    First recall existing shared records/memory. Give the exact missing facts,
    units, period and scope where relevant. Do not guess, search private data on
    the public web, or wait in a loop. Only Hinter can supply public guidance.
    """
    from .assistance import create_request

    return json.dumps(
        create_request(runtime, summary, needed_information, reason), ensure_ascii=False
    )


@tool
def recall_records(runtime: ToolRuntime, query: str = "", limit: int = 20) -> str:
    """Find owner-scoped meeting records with IDs and revisions. Excludes private notes."""
    run, actor, thread = current_grant(runtime.context)
    with transaction() as c:
        q = (
            select(records)
            .join(threads, threads.c.id == records.c.thread_id)
            .join(workspaces, workspaces.c.id == threads.c.workspace_id)
            .where(
                workspaces.c.owner_user_id == run["owner_user_id"],
                records.c.visibility == "user_shared",
                records.c.withdrawn.is_(False),
                records.c.role.in_(["user", "hinter"]),
                records.c.seq >= 1,
            )
        )
        from .retrieval import terms

        tokens = terms(query)
        if tokens:
            q = q.where(or_(*(records.c.text.contains(t, autoescape=True) for t in tokens)))
        from .retrieval import sql_score

        ordering = [sql_score(records.c.text, query).desc()] if query else []
        result = [
            dict(r)
            for r in c.execute(
                q.order_by(*ordering, records.c.created_at.desc()).limit(min(max(limit, 1), 50))
            ).mappings()
        ]
    from .retrieval import excerpt, score

    if query:
        result.sort(key=lambda r: score(r["text"], query), reverse=True)
    return json.dumps(
        [
            {
                **{k: r[k] for k in ("id", "thread_id", "revision", "created_at")},
                "text": excerpt(r["text"], query),
                "source_characters": len(r["text"]),
            }
            for r in result
        ],
        ensure_ascii=False,
    )


@tool
def recall_memory(runtime: ToolRuntime, query: str = "", limit: int = 8) -> str:
    """Read this owner's saved MySQL memory, with real IDs and revisions."""
    run, _, _ = current_grant(runtime.context)
    from .agent_memory import MySQLMemoryManager

    values = MySQLMemoryManager().search(
        query, min(max(limit, 1), 15), user_id=run["owner_user_id"]
    )
    return json.dumps(values, ensure_ascii=False)


@tool
def remember(runtime: ToolRuntime, name: str, content: str) -> str:
    """Save user-confirmed facts/preferences in MySQL. Claim saved only after success."""
    with transaction() as c:
        run, actor, thread = current_grant(runtime.context, c, lock=True)
        if not runtime.tool_call_id:
            raise PermissionError("Tool write requires a native call ID")
        key = hashlib.sha256(runtime.tool_call_id.encode()).hexdigest()
        operation = f"tool:{run['id']}:remember"
        digest = hashlib.sha256(
            json.dumps([name, content], ensure_ascii=False).encode()
        ).hexdigest()
        previous = (
            c.execute(
                select(commands).where(
                    commands.c.owner_user_id == run["owner_user_id"],
                    commands.c.operation == operation,
                    commands.c.key == key,
                )
            )
            .mappings()
            .first()
        )
        if previous:
            if previous["request_hash"] != digest:
                raise ValueError("Tool call idempotency conflict")
            return json.dumps(previous["response"], ensure_ascii=False)
        from .db import memories

        old = (
            c.execute(
                select(memories).where(
                    memories.c.workspace_id == thread["workspace_id"], memories.c.name == name
                )
            )
            .mappings()
            .first()
        )
        result = {
            "saved": True,
            **put_memory(
                actor,
                thread["workspace_id"],
                name[:160],
                content,
                "user_shared",
                old["revision"] if old else None,
                connection=c,
            ),
        }
        c.execute(
            insert(commands).values(
                owner_user_id=run["owner_user_id"],
                operation=operation,
                key=key,
                request_hash=digest,
                response=result,
            )
        )
        return json.dumps(result, ensure_ascii=False)


@tool
async def web_search(
    runtime: ToolRuntime, query: str, max_results: int = 5, domains: list[str] | None = None
) -> str:
    """Find relevant public sources; use domains for official sites and fetch URLs to verify facts.

    Include the exact subject/location and date for current facts. Empty or failed
    searches are not evidence. retrieved_at is lookup time, not publication time.
    """
    current_grant(runtime.context)
    from app.agent.tool_config import load_tool_config
    from app.agent.web_tools import rank_results, search_web

    try:
        config = load_tool_config()
        config.web_search_provider = os.getenv("DUET_WEB_SEARCH_PROVIDER") or (
            "brave" if os.getenv("BRAVE_SEARCH_API_KEY") else config.web_search_provider
        )
        config.searxng_url = os.getenv("SEARXNG_URL") or config.searxng_url
        if not config.web_search_enabled:
            raise ValueError("Search disabled")
        config.timeout_seconds = min(config.timeout_seconds, 12)
        async with asyncio.timeout(15):
            result = await search_web(config, query, min(10, max(1, max_results)), domains)
        sources = [r for r in result["results"] if r.get("source_kind") == "current_weather"]
        others = [r for r in result["results"] if r.get("source_kind") != "current_weather"]
        result["results"] = [*sources, *rank_results(query, others)]
        result["retrieved_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        if not result["results"]:
            result.update(
                ok=False,
                error="NO_RELEVANT_RESULTS",
                message="没有找到与查询相关的来源，请调整关键词；不要据此推断实时事实。",
            )
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        # A network failure is a bounded tool result, never a fabricated current fact.
        return json.dumps(
            {
                "ok": False,
                "error": "SEARCH_UNAVAILABLE",
                "message": "网络搜索暂不可用，请说明无法获取实时信息。",
            },
            ensure_ascii=False,
        )


@tool
def web_fetch(runtime: ToolRuntime, url: str) -> str:
    """Read a public source to verify search snippets; each redirect is re-authorized.

    Returns the final source URL and retrieval time. A retrieval timestamp is
    not a publication date or proof that the content describes today's facts.
    """
    current_grant(runtime.context)
    original = url
    try:
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            for redirect in range(4):
                parsed = urlparse(url)
                port = 443 if parsed.scheme == "https" else 80
                if (
                    parsed.scheme not in ("https", "http")
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.port not in (None, port)
                ):
                    raise ValueError("Only public web URLs are allowed")
                addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
                if not addresses or any(
                    not ipaddress.ip_address(a[4][0]).is_global for a in addresses
                ):
                    raise ValueError("Non-public destination denied")
                host = parsed.hostname.encode("idna").decode("ascii")
                target = httpx.URL(url).copy_with(host=addresses[0][4][0])
                with client.stream(
                    "GET",
                    target,
                    headers={"User-Agent": "DuetMeetingAssistant/1.0", "Host": host},
                    extensions={"sni_hostname": host} if parsed.scheme == "https" else {},
                ) as response:
                    if response.is_redirect:
                        if redirect == 3 or not response.headers.get("location"):
                            raise ValueError("Too many redirects")
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    body = bytearray()
                    for part in response.iter_bytes():
                        body.extend(part)
                        if len(body) > 1024 * 1024:
                            raise ValueError("Page exceeds 1 MiB")
                    break
        from bs4 import BeautifulSoup

        doc = BeautifulSoup(bytes(body), "html.parser")
        title = doc.title.get_text(" ", strip=True) if doc.title else ""
        date = doc.find("meta", attrs={"property": "article:published_time"})
        for tag in doc(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()
        content = (doc.find("main") or doc.find("article") or doc).get_text(" ", strip=True)
        if not content:
            raise ValueError("Empty page")
        return json.dumps(
            {
                "ok": True,
                "url": url,
                "title": title,
                "published_at": date.get("content") if date else None,
                "retrieved_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "content": content[:24000],
                "truncated": len(content) > 24000,
            },
            ensure_ascii=False,
        )
    except (httpx.HTTPError, OSError, ValueError):
        return json.dumps(
            {
                "ok": False,
                "error": "SOURCE_UNAVAILABLE",
                "url": original,
                "message": "未能读取来源正文；不能将搜索摘要当成已核实的事实。",
            },
            ensure_ascii=False,
        )


class EmployeeAuthorization:
    name = "duet-mysql-rbac"

    def __init__(self, **kwargs):
        pass

    def _allow(self, principal, resource, target):
        from deerflow.authz.provider import AuthzDecision

        try:
            run, _, _ = current_grant(
                {"user_id": principal.user_id, "authz_attributes": principal.attributes}
            )
            projection = run["kind"] == "projection"
            permitted = (
                resource in ("model", "skill", "sandbox")
                or resource == "tool"
                and target in SAFE_TOOLS
                and not projection
            )
        except Exception:
            permitted = False
        return AuthzDecision(allow=permitted, policy_id="duet-owner-rbac-v1")

    def authorize(self, request):
        return self._allow(request.principal, request.resource, request.target)

    async def aauthorize(self, request):
        return await asyncio.to_thread(self.authorize, request)

    def filter_resources(self, principal, resource_type, candidates):
        return [x for x in candidates if self._allow(principal, resource_type, x).allow]
