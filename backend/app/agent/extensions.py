"""Local operator CLI: python -m app.agent.extensions --help (from backend/)."""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys

from app.agent.tool_config import (
    PROJECT_ROOT,
    McpServer,
    ToolConfig,
    config_path,
    load_tool_config,
)
from app.agent.tools import ToolRuntime


def save(config: ToolConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


async def inspect(config: ToolConfig, probe: bool) -> dict:
    runtime = ToolRuntime(config)
    if probe:
        async with runtime:
            return {
                "tools": list(runtime.tools),
                "skills": runtime.skills.inventory(),
                "unavailable_mcp": runtime.unavailable,
            }
    return {
        "tools": list(runtime.tools),
        "skills": runtime.skills.inventory(),
        "mcp_servers": {
            name: {"transport": server.transport, "enabled": server.enabled}
            for name, server in config.mcp_servers.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Duet project-local tools and skills")
    commands = parser.add_subparsers(dest="action", required=True)
    status = commands.add_parser("status", help="List tools, skills and missing dependencies")
    status.add_argument("--probe", action="store_true", help="Connect configured MCP servers")
    skill = commands.add_parser(
        "skill-add", help="Install one skill via AgentBuddy into this project"
    )
    skill.add_argument("source")
    skill.add_argument("--skill", required=True)
    skill.add_argument("--registry", default="https://registry.npmjs.org")
    mcp = commands.add_parser("mcp-add", help="Register a server; stdio packages launch on use")
    mcp.add_argument("name")
    target = mcp.add_mutually_exclusive_group(required=True)
    target.add_argument("--command")
    target.add_argument("--url")
    mcp.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    mcp.add_argument("--header", action="append", default=[], metavar="KEY=VALUE")
    mcp.add_argument("--allow-tool", action="append")
    mcp.add_argument("--arg", action="append", default=[], help="Repeat; use --arg=-y for flags")
    remove = commands.add_parser("mcp-remove", help="Remove a registered server")
    remove.add_argument("name")
    args = parser.parse_args(argv)
    config = load_tool_config()
    if args.action == "status":
        print(json.dumps(asyncio.run(inspect(config, args.probe)), indent=2, ensure_ascii=False))
    elif args.action == "skill-add":
        # Source/name are argv, never shell text; no global install, other agents or skills.
        if args.source.startswith("-") or not re.fullmatch(r"[\w.-]+", args.skill):
            parser.error("Expected a source and one skill name (not flags)")
        env = {**os.environ, "npm_config_registry": args.registry}
        result = subprocess.run(
            [
                "npx",
                "agentbuddy@latest",
                "skill",
                "add",
                args.source,
                "--skill",
                args.skill,
                "-a",
                "codex",
                "-y",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
        )
        return result.returncode
    elif args.action == "mcp-add":
        if not re.fullmatch(r"[\w.-]+", args.name):
            parser.error("Server name must contain letters, numbers, '.', '_' or '-'")
        if args.name in config.mcp_servers:
            parser.error("Server already exists; remove it before replacing it")

        def pairs(values: list[str]) -> dict[str, str]:
            if any("=" not in value or not value.split("=", 1)[0] for value in values):
                parser.error("Expected KEY=VALUE")
            return dict(value.split("=", 1) for value in values)

        config.mcp_servers[args.name] = McpServer(
            transport="http" if args.url else "stdio",
            url=args.url or "",
            command=args.command or "",
            args=args.arg,
            env=pairs(args.env),
            headers=pairs(args.header),
            allowed_tools=args.allow_tool,
        )
        save(config)
        print(f"Registered MCP server: {args.name}")
    else:
        if args.name not in config.mcp_servers:
            parser.error("MCP server does not exist")
        del config.mcp_servers[args.name]
        save(config)
        print(f"Removed MCP server: {args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
