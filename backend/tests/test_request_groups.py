"""A connected request gets one answer, not a fallback for every metric."""

import asyncio
from unittest.mock import AsyncMock

from app.agent.request_groups import coalesce_requests

TEXT = "I'd like to know Tiktok's annual revenue, annual tax payments, and its daily and monthly active users."
PARTS = ["I'd like to know Tiktok's annual revenue", "annual tax payments", "its daily and monthly active users"]
QUOTE = "The supplied monthly active user count is 2.3 billion."


def plans():
    return [{
        "text": part, "category": "data_lookup", "data_scope": "public",
        "action": "lookup" if index == 1 else "answer",
        "evidence_status": "missing" if index == 1 else "partial",
        "evidence_source": None if index == 1 else "provided",
        "evidence_quotes": [] if index == 1 else [QUOTE],
        "evidence_gap": "Annual tax payments are missing." if index == 1 else "Reporting year missing.",
        "depends_on": [], "matched_rule_ids": [],
    } for index, part in enumerate(PARTS)]


def test_dependent_metric_fragments_form_one_complete_request():
    result = coalesce_requests(TEXT, plans(), "")
    assert len(result) == 1
    assert result[0]["text"] == TEXT
    assert result[0]["subrequests"] == PARTS
    assert result[0]["action"] == "lookup"
    assert result[0]["evidence_quotes"] == [QUOTE]
    assert result[0]["evidence_status"] == "partial"
    assert "tax payments are missing" in result[0]["evidence_gap"]
    assert result[0]["web_query"] == TEXT


def test_grouping_preserves_privacy_and_operator_approval_boundaries():
    items = plans()
    items[1]["data_scope"] = "internal"
    assert len(coalesce_requests(TEXT, items, "")) == 3
    items = plans()
    items[1]["matched_rule_ids"] = ["approval"]
    rules = "[approval / require_human / session] Confirm tax answers."
    assert len(coalesce_requests(TEXT, items, rules)) == 3


def test_independent_entities_and_record_ids_are_not_coalesced():
    items = plans()
    for index, plan in enumerate(items):
        plan.update(answer_group=f"g{index}", entity_key=f"record-{index}")
    assert len(coalesce_requests(TEXT, items, "")) == 3
    record_text = "Read record A12 and B34"
    items = [{"text": part, "category": "data_lookup", "data_scope": "internal"}
             for part in ["Read record A12", "B34"]]
    assert len(coalesce_requests(record_text, items, "")) == 2


def test_grouping_remaps_external_dependencies():
    items = plans()
    extra = {"text": "Explain the result.", "category": "routine", "action": "answer",
             "data_scope": "none", "depends_on": [0, 1, 2]}
    result = coalesce_requests(TEXT + " Explain the result.", items + [extra], "")
    assert len(result) == 2 and result[1]["depends_on"] == [0]


def test_current_publisher_emits_one_grouped_answer_without_unknown_cards(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.plan.return_value = plans()

        async def stream(question, *args, context=None, **kwargs):
            assert question == TEXT
            assert context["subrequests"] == PARTS
            assert context["retrieval_mode"] == "mixed"
            yield "The supplied MAU is 2.3 billion. Annual tax payments are still missing."

        calls.reasoner.answer_stream = stream
        await service.submit(sid, TEXT)
        await drain(service)
        state = service.get(sid)
        assert len(state["questions"]) == len(state["answers"]) == 1
        assert state["answers"][0]["status"] == "completed"
        assert "I don't know." not in state["answers"][0]["text"]
        calls.search.lookup.assert_awaited_once_with(TEXT)

    asyncio.run(scenario())


def test_publication_check_is_not_a_streaming_coverage_gate(tmp_path):
    from test_live_tools import setup

    from app.agent.call_reasoner import CallReasoner

    async def scenario():
        _, calls, _ = setup(tmp_path)
        calls.reasoner.ask = AsyncMock(return_value={"approved": True})
        result = await CallReasoner.check(
            calls.reasoner, TEXT, QUOTE, "", "[hint / instruction / session] " + QUOTE,
        )
        assert result["approved"]
        instruction = calls.reasoner.ask.call_args.args[0]
        assert "IN-PROGRESS streaming prefix" in instruction
        assert "incompleteness" in instruction

    asyncio.run(scenario())
