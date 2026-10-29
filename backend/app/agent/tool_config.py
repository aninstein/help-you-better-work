"""Project-local extension configuration. Installation is a local operator action."""

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


class McpServer(BaseModel):
    transport: Literal["stdio", "http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    allowed_tools: list[str] | None = None

    @model_validator(mode="after")
    def validate_transport(self) -> "McpServer":
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP requires command")
        if self.transport == "http" and not self.url.startswith(("https://", "http://")):
            raise ValueError("HTTP MCP requires an http(s) URL")
        return self


class ToolConfig(BaseModel):
    workspace: str = "."
    skill_dirs: list[str] = Field(default_factory=lambda: [".agents/skills", ".codex/skills"])
    web_search_enabled: bool = True
    web_search_provider: Literal["bing", "brave", "searxng"] = "bing"
    searxng_url: str = ""
    lark_doc_enabled: bool = True
    lark_cli: str = "lark-cli"
    bash_enabled: bool = True
    timeout_seconds: int = Field(default=30, ge=1, le=180)
    max_output_chars: int = Field(default=18000, ge=1000, le=100000)
    max_rounds: int = Field(default=8, ge=1, le=20)
    max_calls: int = Field(default=24, ge=1, le=100)
    mcp_servers: dict[str, McpServer] = Field(default_factory=dict)

    def resolve(self, path: str) -> Path:
        value = Path(path).expanduser()
        return (value if value.is_absolute() else PROJECT_ROOT / value).resolve()


def config_path() -> Path:
    return Path(os.getenv("DUET_TOOLS_CONFIG", str(PROJECT_ROOT / "agent-tools.json")))


def load_tool_config() -> ToolConfig:
    path = config_path()
    return ToolConfig.model_validate_json(path.read_text()) if path.exists() else ToolConfig()
