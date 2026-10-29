"""MCP tools client: stdio and Streamable HTTP, JSON/SSE, per-answer sessions.

Only the tools capability is implemented. Sampling, roots and elicitation are not
advertised; legacy HTTP+SSE transport is intentionally unsupported.
"""

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

import httpx

from app.agent.tool_config import McpServer


class McpClient:
    def __init__(self, config: McpServer, cwd: Path, timeout: int) -> None:
        self.config = config
        self.cwd = cwd
        self.timeout = timeout
        self.process: asyncio.subprocess.Process | None = None
        self.http: httpx.AsyncClient | None = None
        self.session_id = ""
        self.version = "2025-03-26"
        self.counter = 0
        self.lock = asyncio.Lock()

    async def __aenter__(self) -> "McpClient":
        try:
            if self.config.transport == "stdio":
                self.process = await asyncio.create_subprocess_exec(
                    self.config.command,
                    *self.config.args,
                    cwd=self.cwd,
                    env={
                        **os.environ,
                        **{k: os.path.expandvars(v) for k, v in self.config.env.items()},
                    },
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=2_000_000,
                    start_new_session=True,
                )
            else:
                self.http = httpx.AsyncClient(
                    timeout=self.timeout,
                    headers={k: os.path.expandvars(v) for k, v in self.config.headers.items()},
                )
            result = await self.request(
                "initialize",
                {
                    "protocolVersion": self.version,
                    "capabilities": {},
                    "clientInfo": {"name": "duet-ai", "version": "0.3.0"},
                },
            )
            self.version = result["protocolVersion"]
            if self.version not in {"2024-11-05", "2025-03-26", "2025-06-18"}:
                raise ValueError("Unsupported MCP protocol version")
            await self.notify("notifications/initialized")
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self.process:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.process.wait()
        if self.http:
            try:
                if self.session_id:
                    await self.http.delete(self.config.url, headers=self.headers())
            except httpx.HTTPError:
                pass
            finally:
                await self.http.aclose()

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.version,
            **({"Mcp-Session-Id": self.session_id} if self.session_id else {}),
        }

    async def notify(self, method: str) -> None:
        async with asyncio.timeout(self.timeout):
            await self.send({"jsonrpc": "2.0", "method": method}, expect_reply=False)

    async def request(self, method: str, params: dict) -> dict:
        async with self.lock, asyncio.timeout(self.timeout):
            self.counter += 1
            message = {"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}
            response = await self.send(message, expect_reply=True)
            if response.get("error"):
                # Do not expose server credentials or raw transport errors to the model.
                raise RuntimeError(f"MCP {method} failed: code {response['error'].get('code')}")
            return response.get("result", {})

    async def send(self, message: dict, expect_reply: bool) -> dict:
        if self.process:
            assert self.process.stdin and self.process.stdout
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()
            if not expect_reply:
                return {}
            while line := await self.process.stdout.readline():
                reply = json.loads(line)
                if reply.get("id") == message["id"] and "method" not in reply:
                    return reply
                if "method" in reply and "id" in reply:
                    # No server-initiated capabilities were advertised. Ping is core.
                    answer = {"jsonrpc": "2.0", "id": reply["id"]}
                    answer.update(
                        {"result": {}}
                        if reply["method"] == "ping"
                        else {"error": {"code": -32601, "message": "Capability not supported"}}
                    )
                    self.process.stdin.write((json.dumps(answer) + "\n").encode())
                    await self.process.stdin.drain()
            raise RuntimeError("MCP server closed its output")
        assert self.http
        async with self.http.stream(
            "POST", self.config.url, json=message, headers=self.headers()
        ) as response:
            response.raise_for_status()
            self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
            if not expect_reply:
                return {}
            if "text/event-stream" in response.headers.get("content-type", ""):
                parts: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        parts.append(line[5:].lstrip())
                    elif not line and parts:
                        reply = json.loads("\n".join(parts))
                        parts = []
                        if reply.get("id") == message["id"] and "method" not in reply:
                            return reply
                raise RuntimeError("MCP event stream ended without a response")
            payload = await response.aread()
            reply = json.loads(payload)
            if reply.get("id") != message["id"]:
                raise RuntimeError("MCP response ID mismatch")
            return reply

    async def list_tools(self) -> list[dict]:
        result: list[dict] = []
        cursor = None
        seen: set[str] = set()
        for _ in range(20):
            page = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            result.extend(page.get("tools", []))
            cursor = page.get("nextCursor")
            if not cursor:
                return result
            if cursor in seen:
                raise RuntimeError("MCP tools pagination repeated a cursor")
            seen.add(cursor)
        raise RuntimeError("MCP tools pagination limit exceeded")

    async def call(self, name: str, arguments: dict) -> dict:
        return await self.request("tools/call", {"name": name, "arguments": arguments})
