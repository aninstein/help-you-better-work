"""LangGraph-style decision graph for real-time call agent decisions.

The graph is intentionally limited to the Agent decision core. Audio transport,
STT/TTS, session locks and streaming publishers stay in the existing services.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.agent.tools import document_links

if TYPE_CHECKING:
    from app.agent.call_reasoner import CallReasoner

GraphState = dict[str, Any]
Node = Callable[[GraphState], Awaitable[GraphState]]

try:  # pragma: no cover - covered through behavior; import depends on runtime extras.
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - keeps local tests usable before deps are installed.
    END = "__end__"
    START = "__start__"
    StateGraph = None  # type: ignore[assignment]


class _LinearDecisionGraph:
    """Small fallback runner with the same node order as the LangGraph graph."""

    def __init__(self, owner: AgentDecisionGraph) -> None:
        self.owner = owner

    async def ainvoke(self, state: GraphState) -> GraphState:
        state = await self.owner.prohibition_gate(state)
        if state.get("result") is not None:
            return state
        state = await self.owner.shortcut_gate(state)
        if state.get("result") is not None:
            return state
        state = await self.owner.route_questions(state)
        return await self.owner.scope_and_policy_gate(state)


class AgentAnswerGraph:
    """Graph nodes for Hinter Gate, memory retrieval and answer generation."""

    def __init__(self, reasoner: CallReasoner) -> None:
        self.reasoner = reasoner
        self.compiled = self._compile()

    def _compile(self) -> Any:
        # Answer generation returns an async stream. Keep this graph on the
        # deterministic runner so streaming objects are not materialized or
        # checkpointed by graph infrastructure.
        return _LinearAnswerGraph(self)

    async def stream(
        self,
        response_service: Any,
        *,
        session_id: str,
        question_id: str,
        question: dict,
        wiki: str,
        constraints: str,
        session_state: dict,
        evidence: str = "",
        supplemental: bool = False,
        on_tool: Any = None,
    ) -> Any:
        graph_state = await self.compiled.ainvoke(
            {
                "response_service": response_service,
                "session_id": session_id,
                "question_id": question_id,
                "question": question,
                "wiki": wiki,
                "constraints": constraints,
                "session_state": session_state,
                "evidence": evidence,
                "supplemental": supplemental,
                "on_tool": on_tool,
                "trace": [],
            }
        )
        return graph_state["stream"]

    async def hinter_gate(self, state: GraphState) -> GraphState:
        service = state["response_service"]
        question = state["question"]
        constraints = state["constraints"]
        if question.get("prohibition_assessment") is not None:
            constraints = service.scoped_constraints(
                state["session_id"],
                constraints,
                question["prohibition_assessment"]["matched_rule_ids"],
            )
        state["constraints"] = constraints
        state["trace"].append("hinter_gate")
        return state

    async def memory_retrieval(self, state: GraphState) -> GraphState:
        service = state["response_service"]
        question = state["question"]
        evidence = state["evidence"]
        # The publisher already assembled Wiki and retrieved evidence for validation.
        wiki = state["wiki"]
        prompt = question["text"]
        if state.get("supplemental"):
            prompt = "Briefly follow up on this earlier question: " + prompt
            question = {**question, "text": prompt}
        state["prompt"] = prompt
        state["wiki_with_evidence"] = wiki
        state["context"] = service.answer_context(state["session_state"], question, evidence)
        state["history"] = service.history(state["session_state"])
        state["trace"].append("memory_retrieval")
        return state

    async def answer_generation(self, state: GraphState) -> GraphState:
        try:
            state["stream"] = self.reasoner.answer_stream(
                state["prompt"],
                state["wiki_with_evidence"],
                state["constraints"],
                state["history"],
                context=state["context"],
                on_tool=state["on_tool"],
            )
        except TypeError:
            # Some tests and lightweight adapters monkeypatch answer_stream with a
            # simple *args-only coroutine generator. Keep the graph boundary stable
            # without forcing every adapter to accept optional runtime hooks.
            state["stream"] = self.reasoner.answer_stream(
                state["prompt"],
                state["wiki_with_evidence"],
                state["constraints"],
                state["history"],
            )
        state["trace"].append("answer_generation")
        return state


class _LinearAnswerGraph:
    """Fallback answer graph runner when LangGraph is unavailable."""

    def __init__(self, owner: AgentAnswerGraph) -> None:
        self.owner = owner

    async def ainvoke(self, state: GraphState) -> GraphState:
        state = await self.owner.hinter_gate(state)
        state = await self.owner.memory_retrieval(state)
        return await self.owner.answer_generation(state)


class AgentDecisionGraph:
    """Explicit graph for AI Intake, Hinter Gate and routing policy decisions."""

    def __init__(self, reasoner: CallReasoner) -> None:
        self.reasoner = reasoner
        self.compiled = self._compile()

    def _compile(self) -> Any:
        if StateGraph is None:
            return _LinearDecisionGraph(self)

        graph = StateGraph(dict)
        graph.add_node("prohibition_gate", self.prohibition_gate)
        graph.add_node("shortcut_gate", self.shortcut_gate)
        graph.add_node("route_questions", self.route_questions)
        graph.add_node("scope_and_policy_gate", self.scope_and_policy_gate)
        graph.add_edge(START, "prohibition_gate")
        graph.add_conditional_edges(
            "prohibition_gate",
            self._next_after_gate,
            {"done": END, "continue": "shortcut_gate"},
        )
        graph.add_conditional_edges(
            "shortcut_gate",
            self._next_after_gate,
            {"done": END, "continue": "route_questions"},
        )
        graph.add_edge("route_questions", "scope_and_policy_gate")
        graph.add_edge("scope_and_policy_gate", END)
        return graph.compile()

    @staticmethod
    def _next_after_gate(state: GraphState) -> str:
        return "done" if state.get("result") is not None else "continue"

    async def plan(
        self,
        text: str,
        wiki: str,
        constraints: str,
        history: list[dict],
        *,
        transcription: dict | None = None,
    ) -> list[dict]:
        runner = (self.compiled if os.getenv("CALL_REASONER_DECISION_GRAPH", "1") != "0"
                  else _LinearDecisionGraph(self))
        state = await runner.ainvoke(
            {
                "text": text,
                "wiki": wiki,
                "constraints": constraints,
                "history": history,
                "transcription": transcription or {},
                "trace": [],
            }
        )
        result = state.get("result")
        if isinstance(result, list):
            return result
        raise ValueError("决策图未产出问题路由结果。")

    async def prohibition_gate(self, state: GraphState) -> GraphState:
        text = str(state["text"])
        constraints = str(state["constraints"])
        prohibition_ids = set(re.findall(r"\[([^\]\n]+) / prohibition /", constraints))
        state["prohibition_ids"] = prohibition_ids
        state["trace"].append("prohibition_gate")
        if not prohibition_ids:
            return state

        assessment = await self.reasoner.assess_prohibition(
            text, str(state["wiki"]), constraints, state["history"]
        )
        state["prohibition_assessment"] = assessment
        matched = assessment["matched_rule_ids"]
        if matched:
            state["result"] = [
                {
                    "text": text,
                    "action": "refuse",
                    "matched_rule_ids": matched,
                    "prohibition_assessment": assessment,
                }
            ]
        return state

    async def shortcut_gate(self, state: GraphState) -> GraphState:
        text = str(state["text"])
        constraints = str(state["constraints"])
        prohibition_ids = state.get("prohibition_ids") or set()
        state["trace"].append("shortcut_gate")

        links = document_links(text)
        if links and "require_human" not in constraints and not prohibition_ids:
            state["result"] = [
                {
                    "text": text,
                    "category": "routine",
                    "action": "answer",
                    "document_urls": links[:5],
                }
            ]
            return state

        return state

    async def route_questions(self, state: GraphState) -> GraphState:
        state["trace"].append("route_questions")
        state["raw_questions"] = await self.reasoner.route_plans(
            state["text"], state["wiki"], state["constraints"], state["history"],
            transcription=state.get("transcription"),
            assessment=state.get("prohibition_assessment"),
        )
        return state

    async def scope_and_policy_gate(self, state: GraphState) -> GraphState:
        state["trace"].append("scope_and_policy_gate")
        state["result"] = await self.reasoner.scope_plans(
            state["text"], state["wiki"], state["constraints"], state["history"],
            state["raw_questions"], state.get("prohibition_assessment"),
        )
        return state


def load_enabled_tool_summary() -> list[dict[str, str]]:
    """Return a small, prompt-safe view of configured internal tools."""

    from app.agent.tool_config import load_tool_config

    config = load_tool_config()
    tools: list[dict[str, str]] = []
    if config.web_search_enabled:
        tools.append(
            {
                "name": "web_search",
                "description": f"Public web lookup through {config.web_search_provider}.",
                "endpoint": "public-web",
            }
        )
    if config.lark_doc_enabled:
        tools.append(
            {
                "name": "lark_doc",
                "description": "Read user-authorized Lark documents supplied by URL.",
                "endpoint": config.lark_cli,
            }
        )
    if config.bash_enabled:
        tools.append(
            {
                "name": "bash",
                "description": "Run bounded local shell commands in the configured workspace.",
                "endpoint": config.workspace,
            }
        )
    for name, server in config.mcp_servers.items():
        if server.enabled:
            tools.append(
                {
                    "name": f"mcp:{name}",
                    "description": "Configured MCP server connector.",
                    "endpoint": server.url or server.command,
                }
            )
    return tools
