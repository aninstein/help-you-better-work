"""Speech-editor uncertainty is advisory unless protected input actually conflicts."""

import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize("question,term", [
    ("I want to know how much tiktok dau.", "tiktok dau"),
    ("What is Kubernetes HPA?", "Kubernetes HPA"),
    ("Look up the public term quasar.", "quasar"),
])
def test_provisional_unknown_term_reaches_semantic_router_and_public_lookup(tmp_path, question, term):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.refiner.refine = AsyncMock(return_value={
            "text": question, "status": "needs_clarification", "protected_conflict": False,
            "unclear_spans": [term], "missing_slots": [{"slot": "metric", "heard": term}],
        })
        calls.reasoner.plan.return_value = [{
            "text": question, "category": "data_lookup", "action": "lookup",
            "data_scope": "public", "web_query": question,
        }]
        await service.submit(sid, question, mode="voice")
        await drain(service)
        calls.reasoner.plan.assert_awaited_once()
        calls.search.lookup.assert_awaited_once_with(question)
        state = service.get(sid)
        assert state["questions"][0]["status"] == "answered"
        assert state["questions"][0]["decision"] == "lookup"
        message = next(m for m in state["messages"] if m["type"] == "talker")
        assert message["transcription_status"] == "corrected"
        assert message["transcription"]["refiner_missing_slots"][0]["heard"] == term
        assert message["transcription"]["resolution"] == "semantic_routing"
        assert all("What did you mean" not in a["text"] for a in state["answers"])

    asyncio.run(scenario())


def test_real_protected_identifier_conflict_cannot_be_searched(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.refiner.refine = AsyncMock(return_value={
            "text": "Query user ID A7B9.", "status": "needs_clarification",
            "protected_conflict": True, "unclear_spans": ["A7B9"],
            "missing_slots": [{"slot": "ID", "heard": "A7B9 or A7B8"}],
        })
        calls.reasoner.plan.return_value = [{
            "text": "Query user ID A7B9.", "action": "clarify",
            "speech_assessment": {"resolution": "needs_clarification", "kind": "identifier"},
            "clarification_question": "Was the ID A7B9 or A7B8?",
        }]
        await service.submit(sid, "Query user ID A7B9.", mode="voice")
        await drain(service)
        assert service.get(sid)["questions"][0]["status"] == "awaiting_user"
        calls.search.lookup.assert_not_awaited()
        calls.reasoner.plan.assert_awaited_once()

    asyncio.run(scenario())


def test_indirect_question_and_entity_homophone_reach_primary_semantics(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        text = "I'd like to know what Lemon 8s daily and monthly active users are."
        calls.refiner.refine = AsyncMock(return_value={
            "text": text, "status": "needs_clarification", "protected_conflict": True,
            "raw_transcript": "I'D LIKE TO KNOW WHAT LEMON ATE AS DAILY AND MONTHLY ACTIVE USERS ARE",
            "final_asr_text": text, "unclear_spans": ["I'd like to know"],
        })
        calls.reasoner.plan.return_value = [{
            "text": text, "action": "lookup", "category": "data_lookup", "data_scope": "public",
            "speech_assessment": {"resolution": "resolved", "kind": "entity_spelling",
                                  "reason": "The final recognizer resolves a product name, not an amount."},
        }]

        async def stream(*args, **kwargs):
            yield "The public lookup completed."

        calls.reasoner.answer_stream = stream
        await service.submit(sid, text, mode="voice")
        await drain(service)
        calls.reasoner.plan.assert_awaited_once()
        calls.search.lookup.assert_awaited_once_with(text)
        message = next(m for m in service.get(sid)["messages"] if m["type"] == "talker")
        assert message["transcription_status"] == "corrected"
        assert message["transcription"]["refiner_protected_conflict"] is True
        assert all("What did you mean" not in a["text"] for a in service.get(sid)["answers"])

    asyncio.run(scenario())


def test_request_frame_is_never_manufactured_into_a_term_clarification(tmp_path):
    from test_live_tools import setup
    from app.agent.transcript_refiner import clear_independent_requests
    from app.application.voice_control import incomplete

    service, _, sid = setup(tmp_path)
    text = "I'd like to know what the daily and monthly active users are."
    assert not incomplete(text)
    _, unclear = clear_independent_requests(text, text)
    assert "I'd like to know" not in unclear
    question = service.replies.clarification(service.get(sid), {
        "text": text, "transcription": {"unclear_spans": ["I'd like to know"]}
    }, {"clarification_question": 'What did you mean by "I\'d like to know"?'})
    assert '"I\'d like to know"' not in question


def test_missing_city_can_still_be_clarified_by_router(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        question = "What's the weather in some ha?"
        calls.refiner.refine = AsyncMock(return_value={
            "text": question, "status": "needs_clarification", "protected_conflict": False,
            "unclear_spans": ["some ha"], "missing_slots": [{"slot": "city", "heard": "some ha"}],
        })
        calls.reasoner.plan.return_value = [{"text": question, "action": "clarify"}]
        await service.submit(sid, question, mode="voice")
        await drain(service)
        state = service.get(sid)
        assert state["questions"][0]["status"] == "awaiting_user"
        assert state["answers"][0]["text"] == "Which city would you like the weather for?"
        calls.reasoner.plan.assert_awaited_once()
        calls.search.lookup.assert_not_awaited()

    asyncio.run(scenario())


def test_internal_unknown_term_does_not_leak_to_public_search(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        question = "How much did our internal Nimbus pilot save?"
        calls.refiner.refine = AsyncMock(return_value={
            "text": question, "status": "needs_clarification", "protected_conflict": False,
            "unclear_spans": ["Nimbus"], "missing_slots": [{"slot": "project", "heard": "Nimbus"}],
        })
        calls.reasoner.plan.return_value = [{
            "text": question, "action": "await_human", "data_scope": "internal",
            "evidence_status": "missing",
        }]
        await service.submit(sid, question, mode="voice")
        await drain(service)
        assert service.get(sid)["questions"][0]["status"] == "held"
        calls.search.lookup.assert_not_awaited()

    asyncio.run(scenario())
