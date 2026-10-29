"""Pinned DeerFlow embedded integration. No custom LLM loop or QA decision graph."""

import json
import os
from pathlib import Path

import yaml

from .checkpoint import MySQLSaver, MySQLStore

ROOT = Path(__file__).resolve().parents[3]
COMMIT = "e9074b7126833b5ca4739bcedcbbed01b211246c"


def selected_model():
    """Reuse the configured model for both DeerFlow and infrequent name refills."""
    raw = json.loads(os.getenv("MODEL_CONFIG_JSON") or "[]")
    raw = [m for m in raw if m.get("enabled", True)]
    if os.getenv("DEEPSEEK_OFFICIAL_API_KEY"):
        model = {
            "provider": "openai",
            "model": os.getenv("DEEPSEEK_OFFICIAL_MODEL", "deepseek-flash"),
            "base_url": os.getenv("DEEPSEEK_OFFICIAL_BASE_URL", "https://api.deepseek.com/v1"),
            "api_key": os.environ["DEEPSEEK_OFFICIAL_API_KEY"],
        }
    elif raw:
        primary = os.getenv("PRIMARY_MODEL")
        model = next((m for m in raw if primary in (m.get("model"), m.get("model_label"))), raw[0])
    else:
        model = {
            "provider": "ollama",
            "model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        }
    return model


def configure():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    data = Path(os.getenv("DUET_DATA_DIR", str(ROOT / ".runtime" / "employee-data")))
    data.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DEER_FLOW_HOME", str(data / "deerflow-files"))
    os.environ.setdefault("DEER_FLOW_PROJECT_ROOT", str(ROOT))
    os.environ.setdefault("DEER_FLOW_DATE_TIMEZONE", "Asia/Shanghai")
    model = selected_model()
    os.environ["DUET_RUNTIME_MODEL_KEY"] = model.get("api_key") or "local"
    ollama = model.get("provider") == "ollama"
    entry = {
        "name": "employee",
        "use": "langchain_ollama:ChatOllama" if ollama else "langchain_openai:ChatOpenAI",
        "model": model["model"],
        "base_url": model["base_url"],
        "temperature": 0.3,
        "max_tokens": 4096,
        "supports_thinking": False,
        "supports_vision": False,
    }
    if not ollama:
        entry.update(api_key="$DUET_RUNTIME_MODEL_KEY", timeout=30, max_retries=0)
        if "deepseek.com" in model["base_url"]:
            entry["extra_body"] = {"thinking": {"type": "disabled"}}
    else:
        entry["base_url"] = os.getenv("OLLAMA_BASE_URL") or model["base_url"]
        entry.update(
            num_ctx=32768, num_predict=4096, num_thread=int(os.getenv("OLLAMA_NUM_THREAD", "4"))
        )
    projection_model = {**entry, "name": "projection"}
    if ollama:
        projection_model["format"] = "json"
    else:
        projection_model["response_format"] = {"type": "json_object"}
    config = {
        "config_version": 62,
        "models": [entry, projection_model],
        "tool_groups": [{"name": "employee"}],
        "tools": [
            {"name": name, "group": "employee", "use": f"app.employee.agent_tools:{name}"}
            for name in (
                "current_time",
                "recall_records",
                "recall_memory",
                "remember",
                "request_hinter",
                "web_search",
                "web_fetch",
            )
        ],
        "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider", "allow_host_bash": False},
        "skills": {"path": str(ROOT / "runtime" / "skills"), "deferred_discovery": True},
        "memory": {
            "enabled": True,
            "injection_enabled": True,
            "manager_class": "app.employee.agent_memory:MySQLMemoryManager",
            "mode": "middleware",
            "backend_config": {"failure_policy": {"read": "fail_closed"}},
        },
        "authorization": {
            "enabled": True,
            "fail_closed": True,
            "provider": {"use": "app.employee.agent_tools:EmployeeAuthorization"},
        },
        "summarization": {"enabled": True},
        "token_budget": {
            "enabled": True,
            "max_tokens": int(os.getenv("AGENT_RUN_TOKEN_BUDGET", "120000")),
        },
        "tool_artifacts": {"enabled": False},
        "task_continuity": {"enabled": False},
        "lead_prompt_overlay": {
            "prepend": (ROOT / "runtime/profiles/personal-employee.md").read_text()
        },
        "subagents": {"enabled": True},
        "title": {"enabled": False},
    }
    path = data / "deerflow.yaml"
    # Interactive/projection children can initialize concurrently.
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", dir=data, delete=False) as target:
        target.write(yaml.safe_dump(config, allow_unicode=True))
        temporary = Path(target.name)
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def make_client():
    from deerflow.client import DeerFlowClient

    path = configure()
    saver = MySQLSaver(require_grant=True)

    class EmployeeDeerFlowClient(DeerFlowClient):
        def _get_runnable_config(self, thread_id, **overrides):
            from .agent_tools import ACTIVE_RUN

            config = super()._get_runnable_config(thread_id, **overrides)
            run = ACTIVE_RUN.get()
            if run is None:
                raise PermissionError("Embedded invocation requires a server-bound Run")
            config["configurable"]["duet_app_run_id"] = run["id"]
            return config

        @staticmethod
        def _tool_message_event(message):
            frame = DeerFlowClient._tool_message_event(message)
            frame.data["status"] = getattr(message, "status", "success")
            return frame

    client = EmployeeDeerFlowClient(
        config_path=str(path),
        checkpointer=saver,
        thinking_enabled=False,
        subagent_enabled=True,
        available_skills={"meeting-assistant", "question-projection"},
    )
    # Bounded integration shim for 2.1.0's provider globals: fail closed instead
    # of allowing native helper/subagent code to select its default memory store.
    from deerflow.runtime.checkpointer import provider as cp_provider
    from deerflow.runtime.store import provider as store_provider

    cp_provider._checkpointer = saver
    store_provider._store = MySQLStore()
    return client
