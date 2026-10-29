"""Unified LangChain tool registry, skills, built-ins and MCP discovery."""

import asyncio
import hashlib
import json
import logging
import os
import re
from contextlib import AsyncExitStack
from typing import Any
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool, StructuredTool

from app.agent.calculator import calculate_expression
from app.agent.mcp_client import McpClient
from app.agent.process import run_process
from app.agent.skills import SkillCatalog
from app.agent.tool_config import ToolConfig
from app.agent.web_tools import fetch_web, search_web

logger = logging.getLogger(__name__)


def document_links(text: str) -> list[str]:
    """Route by resource path, including Feishu/Lark/Doubao custom domains."""
    result = []
    for raw in re.findall(r"https?://[^\s<>\"'）)\]，。；！？]+", text):
        url = raw.rstrip(".,;!?")
        if re.match(r"^/(docx|wiki)/[A-Za-z0-9_-]+(?:/|$)", urlsplit(url).path):
            if url not in result:
                result.append(url)
    return result


class ToolRuntime:
    def __init__(self, config: ToolConfig) -> None:
        self.config = config
        self.skills = SkillCatalog(config)
        self.tools: dict[str, BaseTool] = {calculate_expression.name: calculate_expression}
        self.stack = AsyncExitStack()
        self.unavailable: list[str] = []
        self.register_builtins()

    def add(
        self, name: str, description: str, properties: dict, required: list[str], callback: Any
    ) -> None:
        self.tools[name] = StructuredTool.from_function(
            name=name,
            description=description,
            coroutine=callback,
            args_schema={"type": "object", "properties": properties, "required": required},
        )

    def register_builtins(self) -> None:
        string = {"type": "string"}
        self.add(
            "read_skill",
            "Read an installed skill's SKILL.md or a relative reference file. "
            "Read the skill before using it; then read only relevant references.",
            {"name": string, "resource": {**string, "default": "SKILL.md"}},
            ["name"],
            self.read_skill,
        )
        if self.config.web_search_enabled:
            self.add(
                "web_search",
                "Search the live internet for current or uncertain facts. "
                "Use for news, recent changes, explicit search requests and verification. "
                "Returns source URLs and snippets; web_fetch verifies source text.",
                {
                    "query": string,
                    "max_results": {"type": "integer", "default": 5},
                    "domains": {"type": "array", "items": string},
                },
                ["query"],
                self.web_search,
            )
            self.add(
                "web_fetch",
                "Read a public text/HTML webpage for evidence. "
                "For /docx/ or /wiki/ URLs use lark_doc_read instead.",
                {"url": string},
                ["url"],
                self.web_fetch,
            )
        if self.config.lark_doc_enabled:
            self.add(
                "lark_doc_read",
                "Read a Feishu/Lark document through lark-doc and lark-cli "
                "using the signed-in user. Use for any /docx/ or /wiki/ link, including "
                "doubao.com. Never substitute a public web search for a private document.",
                {
                    "url": string,
                    "scope": {
                        "type": "string",
                        "enum": ["full", "outline", "keyword", "section", "range"],
                        "default": "full",
                    },
                    "keyword": string,
                    "start_block_id": string,
                    "end_block_id": string,
                },
                ["url"],
                self.lark_doc_read,
            )
        if self.config.bash_enabled:
            self.add(
                "bash",
                "Run bash to read, search, create or edit local files, or execute "
                "installed skill commands. Runs with the backend OS user's permissions, "
                "without a sandbox. Use only for operations requested by the user. "
                "Do not install packages or change global settings without authorization.",
                {"command": string, "cwd": string, "timeout_seconds": {"type": "integer"}},
                ["command"],
                self.bash,
            )

    async def __aenter__(self) -> "ToolRuntime":
        await self.stack.__aenter__()
        try:
            for name, config in self.config.mcp_servers.items():
                if not config.enabled:
                    continue
                try:
                    client = await self.stack.enter_async_context(
                        McpClient(
                            config,
                            self.config.resolve(self.config.workspace),
                            self.config.timeout_seconds,
                        )
                    )
                    for item in await client.list_tools():
                        original = item["name"]
                        if (
                            config.allowed_tools is not None
                            and original not in config.allowed_tools
                        ):
                            continue
                        digest = hashlib.sha256(f"{name}:{original}".encode()).hexdigest()[:10]
                        label = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{name}_{original}")[:45]
                        tool_name = f"mcp_{label}_{digest}"

                        async def call(
                            _client: McpClient = client, _name: str = original, **arguments: Any
                        ) -> dict:
                            return await _client.call(_name, arguments)

                        self.tools[tool_name] = StructuredTool.from_function(
                            name=tool_name,
                            description=f"MCP {name}: {item.get('description', '')}",
                            args_schema=item.get(
                                "inputSchema", {"type": "object", "properties": {}}
                            ),
                            coroutine=call,
                        )
                except Exception as exc:
                    self.unavailable.append(f"{name}: {type(exc).__name__}")
                    logger.warning(
                        "mcp_discovery_failed server=%s error=%s", name, type(exc).__name__
                    )
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *args: Any) -> None:
        await self.stack.aclose()

    async def read_skill(self, name: str, resource: str = "SKILL.md") -> dict:
        instructions = self.skills.read(name, resource)
        return {
            "ok": True,
            "name": name,
            "resource": resource,
            "directory": str(self.skills.items[name]["path"].parent),
            "workspace": str(self.config.resolve(self.config.workspace)),
            "instructions": instructions,
        }

    async def web_search(
        self, query: str, max_results: int = 5, domains: list[str] | None = None
    ) -> dict:
        if document_links(query):
            raise ValueError("Private document URLs must use lark_doc_read, not public search")
        return await search_web(self.config, query, max_results, domains)

    async def web_fetch(self, url: str) -> dict:
        if document_links(url):
            return await self.lark_doc_read(url)
        return await fetch_web(self.config, url)

    async def lark_doc_read(
        self,
        url: str,
        scope: str = "full",
        keyword: str = "",
        start_block_id: str = "",
        end_block_id: str = "",
    ) -> dict:
        if not self.config.lark_doc_enabled:
            raise ValueError("lark_doc_read is disabled")
        if not document_links(url) or document_links(url)[0] != url:
            raise ValueError("Expected a /docx/ or /wiki/ document URL")
        # Read the installed skill and the specific fetch reference before executing.
        self.skills.read("lark-doc")
        self.skills.read("lark-doc", "references/lark-doc-fetch.md")
        if scope not in {"full", "outline", "keyword", "section", "range"}:
            raise ValueError("Invalid document scope")
        if scope == "keyword" and not keyword or scope == "section" and not start_block_id:
            raise ValueError("keyword/section scope requires keyword/start_block_id")
        argv = [
            self.config.lark_cli,
            "docs",
            "+fetch",
            "--doc",
            url,
            "--as",
            "user",
            "--doc-format",
            "markdown",
            "--scope",
            scope,
        ]
        for flag, value in [
            ("--keyword", keyword),
            ("--start-block-id", start_block_id),
            ("--end-block-id", end_block_id),
        ]:
            if value:
                argv.extend([flag, value])
        result = await run_process(
            argv,
            self.config.resolve(self.config.workspace),
            self.config.timeout_seconds,
            # Parse the CLI envelope before truncating the document, otherwise any
            # moderately long document becomes invalid JSON rather than evidence.
            1_000_000,
        )
        return normalize_lark_result(url, result, self.config.max_output_chars)

    async def bash(self, command: str, cwd: str = "", timeout_seconds: int = 0) -> dict:
        timeout = min(
            max(timeout_seconds or self.config.timeout_seconds, 1), self.config.timeout_seconds
        )
        directory = self.config.resolve(cwd or self.config.workspace)
        # Avoid accidentally echoing model/search credentials through the shell environment.
        env = {
            k: v
            for k, v in os.environ.items()
            if not any(word in k.upper() for word in ["API_KEY", "TOKEN", "SECRET", "PASSWORD"])
        }
        return await run_process(
            ["bash", "--noprofile", "--norc", "-c", command],
            directory,
            timeout,
            self.config.max_output_chars,
            env,
        )

    async def execute(self, name: str, arguments: dict) -> str:
        try:
            if name not in self.tools:
                raise ValueError(f"Unknown or disabled tool: {name}")
            async with asyncio.timeout(self.config.timeout_seconds + 2):
                result = await self.tools[name].ainvoke(arguments)
            output = (
                json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
            )
            if len(output) > self.config.max_output_chars:
                output = json.dumps(
                    {"truncated": True, "content": output[: self.config.max_output_chars]},
                    ensure_ascii=False,
                )
            logger.info("agent_tool_completed tool=%s", name)
            return output
        except Exception as exc:
            logger.warning("agent_tool_failed tool=%s error=%s", name, type(exc).__name__)
            message = (
                str(exc)
                if isinstance(exc, (ValueError, FileNotFoundError))
                else str(
                    "Tool timed out" if isinstance(exc, TimeoutError) else "Tool execution failed"
                )
            )
            return json.dumps({"ok": False, "error": type(exc).__name__, "message": message})


def normalize_lark_result(url: str, result: dict, limit: int) -> dict:
    """Exit status alone is insufficient: CLI can return an unsuccessful JSON envelope."""
    try:
        payload = json.loads(result.get("stdout", ""))
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    data = payload.get("data") or {}
    document = data.get("document", {}) if isinstance(data, dict) else {}
    content = document.get("content", "") if isinstance(document, dict) else ""
    if (
        result.get("ok")
        and payload.get("ok") is True
        and isinstance(content, str)
        and content.strip()
    ):
        budget = max(500, limit // 2)
        document = {**document, "content": content[:budget]}
        sidecar = json.dumps(document.get("reference_map", {}), ensure_ascii=False)
        if len(sidecar) > limit // 4:
            document["reference_map"] = {"truncated": True, "excerpt": sidecar[: limit // 4]}
        return {
            "ok": True,
            "url": url,
            "identity": payload.get("identity", "user"),
            "data": {"document": document},
            "truncated": len(content) > budget,
        }
    detail = json.dumps(payload, ensure_ascii=False) + result.get("stderr", "")
    if re.search(r"scope|permission|forbidden|access.denied|权限|403|99991672", detail, re.I):
        code = "permission_denied"
        message = "The signed-in Lark user lacks document access or the required scope."
    elif re.search(r"auth|login|token|登录|认证|401", detail, re.I):
        code = "authentication_required"
        message = (
            "Lark CLI requires user sign-in on the backend host before this document can be read."
        )
    else:
        code = "document_read_failed"
        message = "The document reader returned no usable document content."
    return {
        "url": url,
        **result,
        "ok": False,
        "error": code,
        "message": message,
        "stdout": result.get("stdout", "")[: limit // 2],
        "stderr": result.get("stderr", "")[: limit // 4],
    }
