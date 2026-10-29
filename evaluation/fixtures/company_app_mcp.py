#!/usr/bin/env python3
"""Read-only stdio MCP fixture for the synthetic company app evaluation suite."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(request_id: Any, value: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": value})


def error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def read_record(project_id: str) -> dict[str, Any]:
    fixture_path = Path(os.environ["COMPANY_APP_FIXTURE_PATH"])
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    record = fixture.get("records", {}).get("projects", {}).get(project_id)
    if record is None:
        raise KeyError(project_id)
    return {"app_name": fixture["app_name"], "project_id": project_id, **record}


def handle(message: dict[str, Any]) -> None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {})
    if method == "notifications/initialized":
        return
    if method == "initialize":
        result(
            request_id,
            {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "company-app-synthetic-fixture", "version": "1.0.0"},
            },
        )
        return
    if method == "tools/list":
        result(
            request_id,
            {
                "tools": [
                    {
                        "name": "get_project_record",
                        "description": (
                            "Read an authorized synthetic project record from the Star River "
                            "company project app fixture. Only PRJ-2047, PRJ-2051 and PRJ-2054 "
                            "are available. Returns the record fields and its update timestamp. "
                            "This read-only test connector contains no real company data."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "project_id": {
                                    "type": "string",
                                    "enum": ["PRJ-2047", "PRJ-2051", "PRJ-2054"],
                                }
                            },
                            "required": ["project_id"],
                        },
                    }
                ]
            },
        )
        return
    if method == "tools/call":
        if params.get("name") != "get_project_record":
            error(request_id, -32601, "Unknown fixture tool")
            return
        try:
            value = read_record(str(params.get("arguments", {}).get("project_id", "")))
        except (KeyError, OSError, ValueError):
            result(
                request_id,
                {
                    "content": [{"type": "text", "text": "No matching authorized project record."}],
                    "isError": True,
                },
            )
            return
        result(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]},
        )
        return
    if request_id is not None:
        error(request_id, -32601, "Method not found")


def main() -> None:
    for line in sys.stdin:
        try:
            handle(json.loads(line))
        except Exception as exc:  # MCP transport must remain alive and return safe errors.
            message = locals().get("message", {})
            if message.get("id") is not None:
                error(message["id"], -32603, type(exc).__name__)


if __name__ == "__main__":
    main()
