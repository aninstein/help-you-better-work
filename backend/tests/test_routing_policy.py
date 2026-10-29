"""Human approval is an exception; data_lookup alone is not an approval policy."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agent.call_policy import CallPolicy
from app.agent.call_reasoner import CallReasoner
from app.agent.call_search import CallSearch


@pytest.mark.parametrize("action", ["lookup", "await_human"])
@pytest.mark.parametrize("query", ["", "上海天气", "Shanghai weather today"])
def test_public_weather_never_becomes_human_evidence(action, query):
    plan = {
        "text": "Hello, hello, what's the weather in Shanghai?",
        "category": "data_lookup",
        "action": action,
        "web_query": query,
    }
    assert CallPolicy().decide(plan, "") == ("lookup", "public_lookup")
    assert CallSearch.plan_query(plan) == plan["text"]


def test_data_lookup_ambiguity_asks_caller_instead_of_hinter():
    assert CallPolicy().decide(
        {"text": "Which account?", "category": "data_lookup", "action": "clarify"}, ""
    ) == ("clarify", "clarify")


def test_explicit_matched_approval_still_applies_to_public_weather():
    plan = {
        "text": "What's the weather in Shanghai?",
        "category": "data_lookup",
        "action": "await_human",
        "matched_rule_ids": ["rule-1"],
    }
    assert CallPolicy().decide(
        plan, "[rule-1 / require_human / session] Confirm weather answers first."
    ) == ("await_human", "human_confirmation_required")


@pytest.mark.parametrize("action", ["lookup", "await_human"])
def test_private_identifiers_still_need_evidence(action):
    plan = {
        "text": "Query user ID A7B9",
        "category": "data_lookup",
        "action": action,
        "web_query": "user ID A7B9",
    }
    assert CallPolicy().decide(plan, "") == ("await_human", "human_evidence_required")
    assert not CallSearch.plan_query(plan)


def test_router_does_not_replay_historical_human_requests(tmp_path):
    from test_live_tools import setup

    async def scenario():
        _, calls, _ = setup(tmp_path)
        current = "Could you explain how to book a hotel room?"
        calls.reasoner.ask = AsyncMock(
            return_value={
                "questions": [
                    {"text": "Query user ID A7B9", "action": "await_human"},
                    {"text": current, "category": "routine", "action": "answer"},
                ]
            }
        )
        plans = await CallReasoner.plan(
            calls.reasoner, current, "", "", [{"role": "talker", "text": "Query user ID A7B9"}]
        )
        assert len(plans) == 1 and plans[0]["text"] == current
        assert calls.reasoner.policy.decide(plans[0], "") == ("answer", "ordinary")

    asyncio.run(scenario())


def test_invalid_search_keywords_recover_through_current_response_path(tmp_path):
    from test_live_tools import QUESTION, drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.plan.return_value[0]["web_query"] = "Shanghai weather today"
        await service.submit(sid, QUESTION)
        await drain(service)
        question = service.get(sid)["questions"][0]
        assert question["status"] == "answered" and not question["human_reviewed"]
        assert question["risk_reason"] == "public_lookup"
        calls.search.lookup.assert_awaited_once_with(QUESTION)

    asyncio.run(scenario())


def test_router_recovers_once_without_replayable_history(tmp_path):
    from test_live_tools import setup

    async def scenario():
        _, calls, _ = setup(tmp_path)
        current = "Could you describe hotel booking?"
        calls.reasoner.ask = AsyncMock(
            side_effect=[
                {"questions": [{"text": "An old private question", "action": "await_human"}]},
                {"questions": [{"text": current, "action": "answer"}]},
            ]
        )
        plans = await CallReasoner.plan(
            calls.reasoner, current, "", "", [{"role": "talker", "text": "Old question"}]
        )
        assert plans[0]["text"] == current and calls.reasoner.ask.await_count == 2
        assert calls.reasoner.ask.call_args.args[1]["recent_conversation"] == []

    asyncio.run(scenario())
