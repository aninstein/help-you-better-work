"""Regressions for tool execution through the page's current response path."""

import asyncio
import json
from unittest.mock import AsyncMock

from app.adapters.store import Store
from app.agent.call_policy import CallPolicy
from app.agent.call_search import CallSearch
from app.agent.tools import normalize_lark_result
from app.domain.models import SessionRequest
from app.main import create_app

QUESTION = "帮我调用web search查询上海的天气"


def test_public_lookup_not_overridden_by_topic_category():
    policy = CallPolicy()
    assert policy.decide(
        {
            "text": QUESTION,
            "category": "data_lookup",
            "action": "lookup",
            "web_query": "上海的天气",
        },
        "",
    ) == ("lookup", "public_lookup")
    assert (
        policy.decide(
            {
                "text": "查询用户ID A7B9",
                "category": "data_lookup",
                "action": "lookup",
                "web_query": "用户ID A7B9",
            },
            "",
        )[0]
        == "await_human"
    )
    assert not CallSearch.requested_query("不要联网搜索上海天气")


def setup(tmp_path):
    app = create_app(Store(tmp_path / "duet.db"))
    service, calls = app.state.service, app.state.calls
    sid = service.create(SessionRequest())["id"]
    service.organize_wiki = AsyncMock()
    calls.speech.available = lambda: True
    calls.speech.prepare = AsyncMock()
    calls.speech.synthesize = AsyncMock(return_value=(bytes(1920), 24000))
    calls.reasoner.check = AsyncMock(return_value={"approved": True})

    async def structure(question, text, question_id, previous, **kwargs):
        return [{"id": previous[0]["id"] if previous else question_id + ":test",
                 "question_id": question_id, "question": question, "answer": text,
                 "status": "unknown" if text == "I don't know." else "known"}]

    calls.reasoner.structure_reply = AsyncMock(side_effect=structure)

    async def refine(raw, final, recent):
        return {
            "text": raw,
            "status": "corrected",
            "unclear_spans": [],
            "protected_conflict": False,
        }

    calls.refiner.refine = AsyncMock(side_effect=refine)
    calls.reasoner.plan = AsyncMock(
        return_value=[
            {
                "text": QUESTION,
                "category": "data_lookup",
                "action": "lookup",
                "web_query": "上海的天气",
            }
        ]
    )
    calls.search.lookup = AsyncMock(
        return_value=(
            "PUBLIC WEB EVIDENCE: Shanghai weather has been retrieved.",
            ["https://weather.example/today"],
        )
    )

    async def stream(question, wiki, constraints, history, **kwargs):
        assert "Shanghai weather has been retrieved" in wiki
        yield "The retrieved source reports Shanghai's weather."

    calls.reasoner.answer_stream = stream
    return service, calls, sid


async def drain(service):
    async with asyncio.timeout(5):
        while service.tasks:
            await asyncio.gather(*list(service.tasks))


def test_actual_chat_path_executes_lookup_and_projects_result(tmp_path):
    async def scenario():
        service, calls, sid = setup(tmp_path)
        await service.submit(sid, QUESTION)
        await drain(service)
        q = service.get(sid)["questions"][0]
        calls.search.lookup.assert_awaited_once_with("上海的天气")
        assert q["status"] == "answered"
        assert q["work"]["state"] == "result_ready"
        assert q["work"]["tool"] == "web_search"
        assert q["work"]["sources"] == ["https://weather.example/today"]
        assert "work" in service.snapshot(sid, "hinter")["questions"][0]
        assert "work" not in service.snapshot(sid, "talker")["questions"][0]

    asyncio.run(scenario())


def test_human_confirmation_restores_lookup_for_pre_fix_question(tmp_path):
    async def scenario():
        service, calls, sid = setup(tmp_path)
        state = service.get(sid)
        state["messages"].append(
            {
                "id": "source",
                "type": "talker",
                "text": QUESTION,
                "status": "intake_complete",
                "created_at": state["created_at"],
            }
        )
        state["questions"].append(
            {
                "id": "q",
                "text": QUESTION,
                "display_no": "Q-205",
                "category": "data_lookup",
                "status": "held",
                "version": 1,
                "risk_reason": "human_evidence_required",
                "source_message_ids": ["source"],
                "created_at": state["created_at"],
            }
        )
        service.store.save(state)
        await service.replies.resolve(sid, "q", {"decision": "auto_answer"})
        await drain(service)
        calls.search.lookup.assert_awaited_once()
        assert service.get(sid)["questions"][0]["status"] == "answered"

    asyncio.run(scenario())


def test_failed_search_is_not_referred_to_hinter(tmp_path):
    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.search.lookup = AsyncMock(side_effect=TimeoutError())
        await service.submit(sid, QUESTION)
        await drain(service)
        state = service.get(sid)
        assert state["questions"][0]["status"] == "unavailable"
        assert state["questions"][0]["work"]["state"] == "failed"
        assert any(a["text"] == "I don't know." for a in state["answers"])

    asyncio.run(scenario())


def test_cli_exit_zero_does_not_turn_auth_failure_into_document_evidence():
    result = normalize_lark_result(
        "https://example.org/docx/ABC",
        {
            "ok": True,
            "exit_code": 0,
            "stdout": '{"ok":false,"error":"AUTH_REQUIRED"}',
            "stderr": "",
        },
        18000,
    )
    assert not result["ok"] and result["error"] == "authentication_required"


def test_large_document_is_parsed_before_content_is_bounded():
    result = normalize_lark_result(
        "https://example.org/wiki/ABC",
        {
            "ok": True,
            "exit_code": 0,
            "stderr": "",
            "stdout": json.dumps(
                {"ok": True, "data": {"document": {"content": "Source facts. " * 10000}}}
            ),
        },
        18000,
    )
    assert result["ok"] and result["truncated"]
    assert result["data"]["document"]["content"].startswith("Source facts.")
