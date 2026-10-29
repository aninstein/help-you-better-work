"""The current chat/call answer path can execute the same registered tools as drafts."""

import json
import re
import inspect
from pathlib import Path
from typing import Any

from langchain_core.messages import SystemMessage, ToolMessage

from app.agent.tool_config import ToolConfig
from app.agent.tools import ToolRuntime

POLICY = Path(__file__).parent / "prompts" / "tools.md"


async def stream_tool_answer(
    client: Any,
    messages: list,
    config: ToolConfig,
    options: dict | None = None,
    on_tool: Any = None,
):
    async with ToolRuntime(config) as runtime:
        current = [*messages]
        current.insert(
            1,
            SystemMessage(
                content=POLICY.read_text()
                + "\nInstalled skills:\n"
                + runtime.skills.prompt()
                + "\nUnavailable MCP servers:\n"
                + "\n".join(runtime.unavailable)
            ),
        )
        model = client.bind_tools(list(runtime.tools.values()))
        used = 0
        results: dict[str, str] = {}
        force_tool = False
        for turn in range(config.max_rounds + 1):
            exhausted = turn == config.max_rounds or used >= config.max_calls
            if exhausted:
                current.append(
                    SystemMessage(
                        content="Tool budget reached. Answer from obtained "
                        "evidence and state what could not be verified. No more tools."
                    )
                )
            # Tool-bearing turns are private. Only a response with no calls is published.
            target = client if exhausted else model
            if options:
                target = target.bind(**options)
            if force_tool and not exhausted and hasattr(target, "bind"):
                target = target.bind(tool_choice="required")
            response = await target.ainvoke(current)
            if not response.tool_calls:
                if isinstance(response.content, str) and response.content.strip():
                    if re.search(
                        r"\b(?:let me|i(?:'ll| will|’ll))\s+(?:try|check|search|look|"
                        r"fetch|read|find|verify)\b",
                        response.content,
                        re.I,
                    ):
                        if exhausted:
                            raise ValueError("tool_result_is_only_a_future_promise")
                        current.extend(
                            [
                                response,
                                SystemMessage(
                                    content="Your response only "
                                    "promises future work. Execute the requested tool now. "
                                    "If the budget is exhausted, state the actual limitation. "
                                    "Do not publish a promise as the final answer."
                                ),
                            ]
                        )
                        force_tool = True
                        continue
                    yield response.content
                    return
                raise ValueError("empty_tool_answer")
            if exhausted:
                raise ValueError("tool_budget_ignored")
            current.append(response)
            force_tool = False
            for call in response.tool_calls:
                key = call["name"] + json.dumps(call["args"], sort_keys=True)
                if key not in results:
                    if used >= config.max_calls:
                        output = '{"ok":false,"error":"Tool execution budget exhausted"}'
                    else:
                        used += 1
                        output = await runtime.execute(call["name"], call["args"])
                        results[key] = output
                        if on_tool:
                            trace = on_tool(call["name"], call["args"], output)
                            if inspect.isawaitable(trace):
                                await trace
                else:
                    output = results[key]
                current.append(ToolMessage(content=output, tool_call_id=call["id"]))
