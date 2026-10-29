"""Public statistics search first; user-designated internal outcomes need evidence."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agent.call_policy import CallPolicy
from app.agent.call_search import CallSearch

AICO = "I want to know how the efficiency improvement rate is affected by aico"
PUBLIC = [
    "How much DAU of TikTok?",
    "What is TikTok's annual revenue?",
    "TikTok monthly active users in 2025",
    "查询 TikTok 的日活和营收",
    "我们想知道 TikTok 的 DAU",
    "Did TikTok revenue reach 10000000 dollars?",
]


@pytest.mark.parametrize("text", PUBLIC)
@pytest.mark.parametrize("action", ["answer", "lookup", "await_human", "limited_answer"])
def test_public_metric_names_cannot_trigger_human_approval(text, action):
    plan = {"text": text, "category": "data_lookup", "action": action}
    assert CallPolicy().decide(plan, "") == ("lookup", "public_lookup")
    assert CallSearch.allowed(text, text, "routine")
    assert CallSearch.plan_query(plan) == text


@pytest.mark.parametrize(
    "text",
    [
        "Query our internal TikTok DAU dashboard",
        "What is our app's DAU today?",
        "查询我们公司昨天的日活",
        "Query user ID A7B9",
    ],
)
@pytest.mark.parametrize("action", ["answer", "lookup", "await_human"])
def test_explicit_internal_data_never_leaks_through_a_public_query(text, action):
    plan = {
        "text": text,
        "category": "data_lookup",
        "action": action,
        "web_query": "TikTok DAU",
    }
    assert CallPolicy().decide(plan, "") == ("await_human", "human_evidence_required")
    assert not CallSearch.plan_query(plan)
    assert not CallSearch.allowed(text, text, "routine")


@pytest.mark.parametrize("action", ["answer", "lookup", "await_human"])
def test_operator_confirmation_rule_precedes_public_lookup(action):
    plan = {
        "text": "What is TikTok's revenue?",
        "category": "data_lookup",
        "action": action,
        "matched_rule_ids": ["rule-1"],
    }
    assert CallPolicy().decide(
        plan, "[rule-1 / require_human / session] Confirm revenue answers first."
    ) == ("await_human", "human_confirmation_required")


def test_generic_hint_conflict_is_not_a_new_approval_rule():
    assert CallPolicy().decide(
        {
            "text": "TikTok DAU",
            "category": "data_lookup",
            "action": "await_human",
            "reason": "constraint_conflict",
            "matched_rule_ids": ["invented"],
        },
        "[style-1 / style / session] Be concise.",
    ) == ("lookup", "public_lookup")


@pytest.mark.parametrize("text", ["What is DAU?", "What does DAU mean?", "DAU 是什么？"])
def test_metric_definition_is_answered_without_a_lookup(text):
    assert CallPolicy().decide(
        {"text": text, "category": "data_lookup", "action": "answer"}, ""
    ) == ("answer", "ordinary")
    assert not CallSearch.requested_query(text)


def test_no_search_request_does_not_become_human_approval():
    text = "Do not search TikTok DAU"
    assert not CallSearch.requested_query(text)
    assert CallPolicy().decide(
        {"text": text, "category": "data_lookup", "action": "await_human"}, ""
    ) == ("limited_answer", "evidence_unavailable")


def test_matching_internal_facts_can_still_answer_directly():
    assert CallPolicy().decide(
        {"text": AICO, "category": "data_lookup", "action": "answer", "uses_wiki_facts": True},
        "",
    ) == ("answer", "ordinary")


def test_current_response_flow_splits_public_metric_and_internal_outcome(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.plan.return_value = [
            {"text": PUBLIC[0], "category": "data_lookup", "action": "await_human"},
            {"text": AICO, "category": "data_lookup", "action": "answer", "data_scope": "internal"},
        ]
        await service.submit(sid, PUBLIC[0] + " " + AICO)
        await drain(service)
        public, internal = service.get(sid)["questions"]
        assert public["status"] == "answered" and not public["human_reviewed"]
        assert public["work"]["state"] == "result_ready"
        assert internal["status"] == "held" and internal.get("assistance_request")
        assert internal["risk_reason"] == "human_evidence_required"
        calls.search.lookup.assert_awaited_once_with(PUBLIC[0])

    asyncio.run(scenario())


def test_public_search_failure_is_unavailable_not_human_or_answered(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.plan.return_value = [
            {"text": PUBLIC[0], "category": "data_lookup", "action": "await_human"}
        ]
        calls.search.lookup = AsyncMock(side_effect=ValueError("No verifiable public figure"))
        await service.submit(sid, PUBLIC[0])
        await drain(service)
        question = service.get(sid)["questions"][0]
        assert question["status"] == "unavailable"
        assert question["decision"] == "lookup" and not question.get("assistance_request")
        assert question["work"]["state"] == "failed"
        assert all(a["notice"] for a in service.get(sid)["answers"])

    asyncio.run(scenario())


def test_confirmation_is_retained_when_public_search_runs_after_approval(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        await service.guidance.submit(
            sid,
            {"text": "Confirm revenue answers first.", "kind": "require_human"},
            trigger_reply=False,
        )
        rule = service.guidance.records(service.get(sid))[0]
        calls.reasoner.plan.return_value = [
            {
                "text": PUBLIC[1],
                "category": "data_lookup",
                "action": "lookup",
                "matched_rule_ids": [rule["id"]],
            }
        ]
        await service.submit(sid, PUBLIC[1])
        await drain(service)
        question = service.get(sid)["questions"][0]
        assert question["status"] == "held"
        calls.search.lookup.assert_not_awaited()
        await service.replies.resolve(sid, question["id"], {"decision": "auto_answer"})
        await drain(service)
        question = service.get(sid)["questions"][0]
        assert question["human_reviewed"] and question["status"] == "answered"
        assert question["risk_reason"] == "human_confirmation_required"
        calls.search.lookup.assert_awaited_once_with(PUBLIC[1])

    asyncio.run(scenario())
