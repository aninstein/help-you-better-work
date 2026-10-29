"""Future-only Hints cannot reopen, retract or interrupt existing questions."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest


def setup(tmp_path):
    from test_live_tools import setup as base_setup

    service, calls, sid = base_setup(tmp_path)
    state = service.get(sid)
    state["messages"].append({
        "id": "old-message", "type": "talker", "text": "Shanghai weather?",
        "status": "completed", "created_at": state["created_at"],
    })
    state["questions"].append({
        "id": "old-q", "text": "Shanghai weather?", "status": "answered", "version": 1,
        "decision": "lookup", "created_at": state["created_at"],
        "source_message_ids": ["old-message"],
    })
    state["call"] = {
        "id": "call", "status": "listening", "paused": False, "epoch": 1,
        "active_question_id": "old-q", "active_message_id": "old-message", "constraints": [],
    }
    service.store.save(state)
    calls.schedule_retrospection = Mock()
    service.replies.guidance_changed = Mock()
    return service, calls, sid


@pytest.mark.parametrize("text", ["不准回答任何关于Lemon8的问题", "200", "只说两句"])
def test_future_button_never_reanswers_or_infers_an_old_target(tmp_path, text):
    async def scenario():
        service, calls, sid = setup(tmp_path)
        service.replies.apply_guidance = AsyncMock()
        before = service.guidance.snapshot(sid, "old-q")[2]
        await calls.intervene(sid, "call", {
            "action": "add_for_next", "text": text, "question_id": "old-q",
        })
        if service.tasks:
            await asyncio.gather(*list(service.tasks))
        record = service.guidance.records(service.get(sid))[0]
        assert record["effective_from"] == "next_turn"
        assert record["question_id"] is None
        assert service.guidance.snapshot(sid, "old-q")[2] == before
        service.replies.apply_guidance.assert_not_awaited()
        service.replies.guidance_changed.assert_not_called()
        calls.schedule_retrospection.assert_not_called()
        assert not service.get(sid)["answers"]
        assert service.get(sid)["questions"][0]["status"] == "answered"

    asyncio.run(scenario())


def test_future_hint_is_visible_to_new_input_only(tmp_path):
    async def scenario():
        service, calls, sid = setup(tmp_path)
        await calls.intervene(sid, "call", {"action": "add_for_next", "text": "Be concise."})
        record = service.guidance.records(service.get(sid))[0]
        state = service.get(sid)
        state["messages"].append({
            "id": "new-message", "type": "talker", "text": "Next question?",
            "created_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        })
        service.store.save(state)
        assert record["id"] not in service.guidance.snapshot(sid, "old-q")[1]
        assert record["id"] in service.guidance.snapshot(sid, message_id="new-message")[1]

    asyncio.run(scenario())


def test_reference_index_completion_does_not_reopen_old_questions(tmp_path):
    from app.application.guidance import GuidanceService

    async def scenario():
        service, _, sid = setup(tmp_path)
        service.guidance.index_reference = AsyncMock()
        await service.guidance.submit(sid, {
            "text": "Reference facts: the old answer should not be retried.",
            "kind": "reference", "effective_from": "next_turn",
        })
        if service.tasks:
            await asyncio.gather(*list(service.tasks))
        record = service.guidance.records(service.get(sid))[0]
        await GuidanceService.index_reference(service.guidance, sid, record)
        while service.tasks:
            await asyncio.gather(*list(service.tasks))
        service.calls.reasoner.plan.assert_not_awaited()
        assert not service.get(sid)["answers"]

    asyncio.run(scenario())


def test_correct_now_still_explicitly_reanswers_current_question(tmp_path):
    async def scenario():
        service, calls, sid = setup(tmp_path)
        service.replies.apply_guidance = AsyncMock()
        await calls.intervene(sid, "call", {
            "action": "correct_now", "text": "Use the updated answer.",
        })
        while service.tasks:
            await asyncio.gather(*list(service.tasks))
        record = service.guidance.records(service.get(sid))[0]
        assert record["question_id"] == "old-q" and record["effective_from"] == "now"
        service.replies.apply_guidance.assert_awaited_once()

    asyncio.run(scenario())
