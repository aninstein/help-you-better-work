"""Semantic cleanup precedes routing and search; filler is never a missing fact."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

from app.agent import call_search
from app.agent.call_search import CallSearch, relevant_results
from app.agent.tool_config import ToolConfig
from app.agent.tool_loop import stream_tool_answer
from app.agent.tools import ToolRuntime
from app.agent.transcript_refiner import TranscriptRefiner


class RefineModel:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def bind(self, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        return SimpleNamespace(content=json.dumps(self.payload))


def refiner(payload):
    model = RefineModel(payload)
    settings = SimpleNamespace(model="fast", model_label="fast", base_url="http://local")
    return TranscriptRefiner(SimpleNamespace(clients=[(settings, model)])), model


def test_high_quality_asr_still_gets_semantic_cleanup():
    editor, model = refiner(
        {
            "text": "What's the weather in Shanghai?",
            "status": "corrected",
            "removed_spans": ["OK, um,"],
            "unclear_spans": [],
            "missing_slots": [],
        }
    )
    raw = "OK, um, what's the weather in Shanghai?"
    final = {
        "text": raw,
        "status": "ready",
        "quality": {
            "evidence": "whisper_segment_and_word_scores",
            "min_word_probability": 0.99,
            "min_avg_logprob": 0,
            "max_no_speech_prob": 0,
            "max_compression_ratio": 1,
        },
    }
    result = asyncio.run(editor.refine(raw, final, []))
    assert model.calls == 1
    assert result["text"] == "What's the weather in Shanghai?"
    assert result["status"] == "corrected"


def test_noisy_preface_removed_without_guessing_city():
    raw = "NO NO MURM I NO I I SAY WHAT'S THE WAITER IN SOME HA"
    editor, _ = refiner(
        {
            "text": "What's the weather in some ha?",
            "status": "needs_clarification",
            "removed_spans": ["NO NO MURM I NO I I SAY"],
            "unclear_spans": ["MURM", "some ha"],
            "missing_slots": [{"slot": "city", "heard": "some ha"}],
        }
    )
    result = asyncio.run(editor.refine(raw, {}, []))
    assert result["text"] == "What's the weather in some ha?"
    assert result["unclear_spans"] == ["some ha"]
    assert not result["protected_conflict"]
    assert result["missing_slots"][0]["slot"] == "city"


def test_disputed_negation_and_identifier_remain_protected():
    editor, _ = refiner({"text": "Query user ID A7B8.", "status": "corrected", "unclear_spans": []})
    result = asyncio.run(editor.refine("DO NOT QUERY USER ID A7B9", {}, []))
    assert result["status"] == "needs_clarification"
    assert result["protected_conflict"]
    assert "A7B9" in result["text"] and "not" in result["text"]


def test_repeated_prohibition_is_deduplicated_but_not_deleted():
    editor, _ = refiner(
        {"text": "Do not query user ID A7B9.", "status": "corrected", "unclear_spans": []}
    )
    result = asyncio.run(editor.refine("DO NOT, DO NOT QUERY USER ID A7B9", {}, []))
    assert result["status"] == "corrected"
    assert result["text"] == "Do not query user ID A7B9."


def test_refusal_is_not_discarded_as_filler():
    editor, _ = refiner(
        {"text": "Thanks.", "status": "corrected", "removed_spans": ["No,"], "unclear_spans": []}
    )
    result = asyncio.run(editor.refine("No, thanks.", {}, []))
    assert result["protected_conflict"] and result["text"] == "No, thanks."


def test_clean_final_asr_does_not_turn_false_starts_into_prohibition():
    raw = "NO NO MURM I NO I I SAY WHAT'S THE WAITER IN SOME HA"
    editor, _ = refiner(
        {
            "text": "What's the weather in Shanghai?",
            "status": "corrected",
            "removed_spans": ["NO NO MURM I NO I I SAY"],
            "unclear_spans": [],
        }
    )
    result = asyncio.run(editor.refine(raw, {"text": "What's the weather in Shanghai?"}, []))
    assert result["status"] == "corrected" and not result["protected_conflict"]


def test_actual_response_path_routes_effective_input_once(tmp_path):
    from test_live_tools import QUESTION, drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.refiner.refine = AsyncMock(
            return_value={
                "text": QUESTION,
                "status": "corrected",
                "unclear_spans": [],
                "protected_conflict": False,
                "removed_spans": ["嗯嗯"],
            }
        )
        original = "嗯嗯，帮我帮我调用web search查询上海的天气"
        await service.submit(sid, original)
        await drain(service)
        message = next(m for m in service.get(sid)["messages"] if m["type"] == "talker")
        assert message["text"] == QUESTION and message["original_text"] == original
        assert calls.reasoner.plan.call_args.args[0] == QUESTION
        assert calls.refiner.refine.await_count == 1
        await service.replies.process(sid, message["id"])
        assert calls.refiner.refine.await_count == 1

    asyncio.run(scenario())


def test_only_filler_does_not_create_a_question(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.refiner.refine = AsyncMock(return_value={"text": "", "status": "no_content"})
        await service.submit(sid, "Um, uh, hmm.")
        await drain(service)
        assert not service.get(sid)["questions"] and not service.get(sid)["answers"]
        calls.reasoner.plan.assert_not_awaited()

    asyncio.run(scenario())


def test_clarification_targets_missing_city_not_filler(tmp_path):
    from test_live_tools import setup

    service, _, sid = setup(tmp_path)
    message = {
        "text": "What's the weather in some ha?",
        "transcription": {
            "raw_transcript": "NO NO MURM ...",
            "unclear_spans": ["some ha"],
            "missing_slots": [{"slot": "city", "heard": "some ha"}],
            "protected_conflict": False,
        },
    }
    assert (
        service.replies.clarification(
            service.get(sid), message, {"clarification_question": 'What does "MURM" mean?'}
        )
        == "Which city would you like the weather for?"
    )


def test_search_rejects_ok_article_and_really_executes_alternate_query(monkeypatch):
    first, second = "Shanghai weather today", "Shanghai official weather forecast"
    normalize = AsyncMock(return_value=first)
    assess = AsyncMock(
        side_effect=[{"answer_supported": False, "next_query": second}, {"answer_supported": True}]
    )
    search = AsyncMock(
        side_effect=[
            {"results": [{"title": "Shanghai climate", "url": "https://a.example/climate"}]},
            {"results": [{"title": "Shanghai weather", "url": "https://b.example/weather"}]},
        ]
    )
    fetch = AsyncMock(
        side_effect=[
            {"url": "https://a.example/climate", "content": "General climate."},
            {"url": "https://b.example/weather", "content": "Current weather observations."},
        ]
    )
    monkeypatch.setattr(call_search, "search_web", search)
    monkeypatch.setattr(call_search, "fetch_web", fetch)
    body, sources = asyncio.run(
        CallSearch(normalize, assess).lookup("OK, what's the weather in Shanghai?")
    )
    assert search.await_count == 2 and fetch.await_count == 2
    assert search.call_args_list[1].args[1] == second
    assert sources == ["https://b.example/weather"] and "attempt 2" in body
    assert (
        relevant_results(
            "OK, what's the weather in Shanghai?",
            [{"title": "OK", "url": "https://en.wikipedia.org/wiki/OK"}],
        )
        == []
    )


def test_future_search_promise_is_executed_before_final_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ToolRuntime, "web_search", AsyncMock(return_value={"ok": True, "results": []})
    )

    class Model:
        def __init__(self):
            self.responses = iter(
                [
                    AIMessage(content="I couldn't find that. Let me try a more specific query."),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "search-2",
                                "name": "web_search",
                                "args": {"query": "Shanghai weather today"},
                            }
                        ],
                    ),
                    AIMessage(content="The search returned no verified current conditions."),
                ]
            )
            self.forced = False

        def bind_tools(self, tools):
            return self

        def bind(self, **kwargs):
            self.forced |= kwargs.get("tool_choice") == "required"
            return self

        async def ainvoke(self, messages):
            return next(self.responses)

    async def scenario():
        model = Model()
        text = "".join(
            [
                part
                async for part in stream_tool_answer(
                    model, [], ToolConfig(workspace=str(tmp_path), skill_dirs=[])
                )
            ]
        )
        assert model.forced and "Let me try" not in text
        ToolRuntime.web_search.assert_awaited_once()

    asyncio.run(scenario())
