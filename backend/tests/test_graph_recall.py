"""Graph runners preserve the current request, speech intent and supplied facts."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.call_reasoner import CallReasoner


@pytest.mark.parametrize("graph_mode", ["1", "0"])
def test_graph_recalls_internal_metrics_despite_entity_spelling_conflict(
    tmp_path, monkeypatch, graph_mode,
):
    from test_live_tools import drain, setup

    async def scenario():
        monkeypatch.setenv("CALL_REASONER_DECISION_GRAPH", graph_mode)
        service, calls, sid = setup(tmp_path)
        text = "I'd like to know how much AICO improved team efficiency and reduced BTS workload."
        fact = "AICO improved team efficiency by 30% and reduced BTS workload by 12 PD."
        service.wiki_for(sid).write("talker", "notes/pilot.md", "# Pilot results\n" + fact)
        calls.refiner.refine = AsyncMock(return_value={
            "text": text, "status": "needs_clarification", "protected_conflict": True,
            "raw_transcript": "I WOULD LIKE TO KNOW HOW MUCH THE A I SEE OW IMPROVED TEAM EFFICIENCY",
            "final_asr_text": text, "unclear_spans": [], "missing_slots": [],
        })
        calls.reasoner.plan = CallReasoner.plan.__get__(calls.reasoner)
        calls.reasoner.ask = AsyncMock(return_value={"questions": [{
            "text": text, "category": "data_lookup", "action": "answer",
            "data_scope": "internal", "evidence_status": "sufficient",
            "evidence_quotes": [fact], "uses_wiki_facts": True,
            "speech_assessment": {"resolution": "resolved", "kind": "entity_spelling"},
        }]})

        async def stream(question, wiki, constraints, history, *, context=None, **kwargs):
            assert question == text
            assert context["retrieval_mode"] == "provided"
            assert context["evidence_quotes"] == [fact]
            assert fact in wiki
            yield fact

        calls.reasoner.answer_stream = stream
        await service.submit(sid, text, mode="voice")
        await drain(service)
        state = service.get(sid)
        assert state["questions"][0]["status"] == "answered"
        assert state["answers"][-1]["text"] == fact
        assert state["questions"][0]["evidence_source"] == "provided"
        assert state["messages"][0]["transcription_status"] == "corrected"
        assert fact in calls.reasoner.ask.call_args.args[1]["supplied_evidence"]
        assert "speech_assessment" in calls.reasoner.ask.call_args.args[0]
        calls.search.lookup.assert_not_awaited()

    asyncio.run(scenario())


def test_weather_tool_result_follows_its_current_question(tmp_path):
    from test_live_tools import setup

    async def scenario():
        _, calls, _ = setup(tmp_path)
        question = "What's the weather today in Beijing?"
        client = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(
            content="Beijing's source reports 13 degrees Celsius.",
        )))
        client.bind = lambda **options: client
        calls.reasoner.response_model = client
        context = {"decision": "lookup", "tool_evidence": {
            "name": "web_search", "arguments": {"query": question},
            "content": "Beijing: 13 degrees Celsius.",
        }}
        result = "".join([part async for part in CallReasoner.answer_stream(
            calls.reasoner, question, "", "",
            [{"role": "talker", "text": "What's the weather today in Shanghai?"}],
            context=context,
        )])
        messages = client.ainvoke.call_args.args[0]
        current = next(i for i, message in enumerate(messages)
                       if isinstance(message, HumanMessage)
                       and message.content.startswith('{"question":'))
        assert json.loads(messages[current].content)["question"] == question
        assert isinstance(messages[current + 1], AIMessage)
        assert messages[current + 1].tool_calls[0]["args"]["query"] == question
        assert isinstance(messages[current + 2], ToolMessage)
        assert result == "Beijing's source reports 13 degrees Celsius."

    asyncio.run(scenario())


def test_answer_graph_does_not_duplicate_completed_evidence(tmp_path):
    from test_live_tools import setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        evidence = "PUBLIC WEB EVIDENCE: Beijing is 13 degrees Celsius."
        question = {"id": "q", "text": "Beijing weather?", "decision": "lookup"}
        state = service.get(sid)
        state["questions"].append(question)

        async def stream(text, wiki, *args, **kwargs):
            assert wiki.count(evidence) == 1
            yield "Beijing is 13 degrees Celsius."

        calls.reasoner.answer_stream = stream
        stream = await calls.reasoner.answer_graph.stream(
            service.replies, session_id=sid, question_id="q", question=question,
            wiki="Wiki facts\n" + evidence, constraints="", session_state=state,
            evidence=evidence,
        )
        assert [part async for part in stream] == ["Beijing is 13 degrees Celsius."]

    asyncio.run(scenario())
