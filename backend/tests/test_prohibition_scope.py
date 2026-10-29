"""A narrow rule cannot spread through refusals or override a negative scope review."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agent.call_policy import CallPolicy
from app.agent.call_reasoner import CallReasoner

RULE = "[rule-1 / prohibition / customer] 不准回答lemon8的任何问题"
ALLOWED = [
    "How do I book that hotel on a tour?",
    "I'd like to know how much ego has improved the teams efficiency "
    "and how much it has saved in p d man hours",
    "And that much.",
    "What does DAU mean?",
    "What's the weather in Shanghai?",
]


@pytest.mark.parametrize("text", ALLOWED)
def test_negative_scope_review_cannot_be_overridden_by_routing(tmp_path, text):
    from test_live_tools import setup

    async def scenario():
        _, calls, _ = setup(tmp_path)
        calls.reasoner.ask = AsyncMock(side_effect=[
            {"matched_rule_ids": [], "subject": "unrelated subject", "scope_reason": "unrelated"},
            {"questions": [{"text": text, "action": "refuse", "matched_rule_ids": ["rule-1"]}]},
        ])
        history = [
            {"role": "talker", "text": "Lemon8 daily active users?"},
            {"role": "ai", "text": "I don't know."},
            {"role": "talker", "text": "A different question?"},
            {"role": "ai", "text": "I don't know."},
        ]
        plans = await CallReasoner.plan(calls.reasoner, text, "", RULE, history)
        assert plans[0]["matched_rule_ids"] == []
        assert CallPolicy().decide(plans[0], RULE)[0] != "block"
        assert calls.reasoner.ask.call_args_list[0].kwargs["review"] is True
        assert calls.reasoner.ask.call_args_list[1].kwargs["review"] is ("efficiency" in text)
        assert calls.reasoner.ask.call_args_list[1].kwargs["max_tokens"] == 1600
        assert calls.reasoner.ask.await_count == 2
        assert plans[0]["prohibition_assessment"]["scope_reason"] == "unrelated"

    asyncio.run(scenario())


def test_mixed_requests_get_independent_scope_assessments(tmp_path):
    from test_live_tools import setup

    async def scenario():
        _, calls, _ = setup(tmp_path)
        banned, allowed = "Lemon8 daily active users?", ALLOWED[0]
        calls.reasoner.ask = AsyncMock(side_effect=[
            {"matched_rule_ids": []},
            {"questions": [
                {"text": banned, "action": "answer"},
                {"text": allowed, "action": "refuse", "matched_rule_ids": ["rule-1"]},
            ]},
            {"matched_rule_ids": ["rule-1"]},
            {"matched_rule_ids": []},
        ])
        plans = await CallReasoner.plan(
            calls.reasoner, banned + " " + allowed, "", RULE, []
        )
        assert CallPolicy().decide(plans[0], RULE)[0] == "block"
        assert CallPolicy().decide(plans[1], RULE)[0] == "answer"
        assert calls.reasoner.ask.await_count == 4

    asyncio.run(scenario())


def test_unrelated_prohibition_does_not_remove_explicit_approval_or_safety(tmp_path):
    from test_live_tools import setup

    async def scenario():
        _, calls, _ = setup(tmp_path)
        rules = RULE + "\n[review-1 / require_human / session] Confirm hotel answers first."
        for extra, expected in [
            ({"matched_rule_ids": ["rule-1", "review-1"]}, "await_human"),
            ({"category": "harmful", "matched_rule_ids": ["rule-1"]}, "refuse"),
        ]:
            calls.reasoner.ask = AsyncMock(side_effect=[
                {"matched_rule_ids": []},
                {"questions": [{"text": ALLOWED[0], "action": "refuse", **extra}]},
            ])
            plans = await CallReasoner.plan(calls.reasoner, ALLOWED[0], "", rules, [])
            assert CallPolicy().decide(plans[0], rules)[0] == expected

    asyncio.run(scenario())


def test_scope_assessment_is_private_diagnostic(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.plan.return_value = [{
            "text": ALLOWED[0], "action": "clarify", "matched_rule_ids": [],
            "clarification_question": "Which city do you want to stay in?",
            "prohibition_assessment": {"scope_reason": "PRIVATE-SCOPE-REASON"},
        }]
        await service.submit(sid, ALLOWED[0])
        await drain(service)
        assert service.snapshot(sid, "hinter")["questions"][0]["prohibition_assessment"]
        assert "prohibition_assessment" not in service.snapshot(sid, "talker")["questions"][0]

    asyncio.run(scenario())


def test_ordinary_booking_route_does_not_search_on_unsolicited_web_query():
    plan = {
        "text": ALLOWED[0], "action": "answer", "category": "open_domain",
        "data_scope": "public", "web_query": ALLOWED[0],
    }
    assert CallPolicy().decide(plan, RULE) == ("answer", "ordinary")


@pytest.mark.parametrize("discloses_banned_content", [False, True])
@pytest.mark.parametrize("other_instruction", [False, True])
def test_publication_filters_only_unmatched_topic_rules(
    tmp_path, discloses_banned_content, other_instruction
):
    from test_live_tools import setup

    from app.application.responses import InvalidSegment, ReplyRun

    async def scenario():
        service, calls, sid = setup(tmp_path)
        ban = await service.guidance.submit(
            sid, {"kind": "prohibition", "text": "Do not discuss Lemon8."},
            trigger_reply=False,
        )
        if other_instruction:
            await service.guidance.submit(
                sid, {"kind": "instruction", "text": "Ask which destination the caller wants."},
                trigger_reply=False,
            )
        state = service.get(sid)
        q = {
            "id": "scope-q", "text": ALLOWED[0], "decision": "answer",
            "prohibition_assessment": {"matched_rule_ids": []},
        }
        state["questions"].append(q)
        service.store.save(state)
        wiki, constraints, _ = service.guidance.snapshot(sid, "scope-q")
        calls.reasoner.assess_prohibition = AsyncMock(return_value={
            "matched_rule_ids": [ban["id"]] if discloses_banned_content else [],
        })
        calls.reasoner.check = AsyncMock(return_value={
            "approved": not discloses_banned_content,
            "reason": "banned content" if discloses_banned_content else "allowed",
        })
        service.replies.ensure = lambda run: None
        run = ReplyRun(sid, "scope-q", "reply", 1, 1)
        unit = "Lemon8 has 100 users." if discloses_banned_content else "Which hotel do you mean?"
        if discloses_banned_content:
            with pytest.raises(InvalidSegment):
                await service.replies.validate(
                    run, unit, wiki + "UNTRUSTED_TOOL_RESULT", constraints, q
                )
        else:
            await service.replies.validate(
                run, unit, wiki + "UNTRUSTED_TOOL_RESULT", constraints, q
            )
        assert calls.reasoner.assess_prohibition.call_args.kwargs["publication"] is True
        assert "UNTRUSTED_TOOL_RESULT" not in calls.reasoner.assess_prohibition.call_args.args[1]
        if other_instruction or discloses_banned_content:
            assert calls.reasoner.check.await_count == 1
            checked_constraints = calls.reasoner.check.call_args.args[3]
            assert (ban["id"] in checked_constraints) is discloses_banned_content
            if other_instruction:
                assert "Ask which destination the caller wants." in checked_constraints
        else:
            calls.reasoner.check.assert_not_awaited()

    asyncio.run(scenario())


def test_failed_refusal_and_control_messages_are_not_new_answer_templates(tmp_path):
    from test_live_tools import setup

    service, _, _ = setup(tmp_path)
    state = {"messages": [
        {"type": "talker", "text": "Lemon8 users?"},
        {"type": "ai", "public_stream": True, "status": "completed", "refusal": True,
         "text": "I don't know."},
        {"type": "ai", "public_stream": True, "status": "interrupted",
         "text": "A failed earlier answer."},
        {"type": "ai", "public_stream": True, "status": "completed",
         "reply_kind": "progress", "text": "I'm still checking."},
        {"type": "ai", "public_stream": True, "status": "completed",
         "text": "A useful completed answer."},
    ]}
    assert service.replies.history(state) == [
        {"role": "talker", "text": "Lemon8 users?"},
        {"role": "ai", "text": "A useful completed answer."},
    ]


def test_generation_does_not_receive_an_unrelated_topic_ban(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        await service.guidance.submit(
            sid, {"kind": "prohibition", "text": "Do not discuss Lemon8."},
            trigger_reply=False,
        )
        calls.reasoner.plan.return_value = [{
            "text": ALLOWED[0], "action": "answer",
            "prohibition_assessment": {"matched_rule_ids": []},
        }]
        calls.reasoner.assess_prohibition = AsyncMock(return_value={"matched_rule_ids": []})

        async def stream(question, wiki, constraints, history, **kwargs):
            assert "Do not discuss Lemon8." not in constraints
            yield "Which hotel and tour operator do you mean?"

        calls.reasoner.answer_stream = stream
        await service.submit(sid, ALLOWED[0])
        await drain(service)
        state = service.get(sid)
        assert state["questions"][0]["status"] == "answered"
        assert state["answers"][-1]["text"] == "Which hotel and tour operator do you mean?"
        assert state["answers"][-1]["status"] == "completed"
        # Publication still sees all constraints and checks the actual content.
        assert "Do not discuss Lemon8." in calls.reasoner.assess_prohibition.call_args.args[2]

    asyncio.run(scenario())


def test_empty_completed_tool_answer_retries_generation_once_without_repeating_tool():
    from langchain_core.messages import AIMessage

    from app.domain.models import ModelSettings

    class Model:
        def __init__(self):
            self.calls = 0

        def bind(self, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            return AIMessage(content="" if self.calls == 1 else "The source reports 12 billion.")

    async def scenario():
        reasoner = CallReasoner.__new__(CallReasoner)
        reasoner.response_model = Model()
        reasoner.response_settings = ModelSettings()
        context = {
            "decision": "lookup", "use_tools": True,
            "tool_evidence": {
                "name": "web_search", "arguments": {"query": "annual revenue"},
                "content": "Synthetic report: annual revenue 12 billion.",
            },
        }
        answer = "".join([
            delta async for delta in reasoner.answer_stream(
                "Annual revenue?", "", "", [], context=context
            )
        ])
        assert answer == "The source reports 12 billion."
        assert reasoner.response_model.calls == 2

    asyncio.run(scenario())


def test_default_hint_and_saved_instruction_share_prohibition_type(tmp_path):
    from test_live_tools import setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.schedule_retrospection = lambda *args: None
        result = await service.guidance.submit(
            sid, {"text": "禁止回答Lemon八的所有问题"}, trigger_reply=False,
        )
        state = service.get(sid)
        record = service.guidance.records(state)[0]
        assert record["kind"] == "prohibition"
        # Simulate an already saved pre-fix record without changing its owner.
        service.store.append_guidance(
            service.guidance.owner(state, "session"), {**record, "kind": "instruction"},
        )
        service.guidance.cache.clear()
        _, constraints, _ = service.guidance.snapshot(sid)
        assert f"[{result['id']} / prohibition / session]" in constraints
        assert service.guidance.records(state)[0]["text"] == "禁止回答Lemon八的所有问题"

    asyncio.run(scenario())


def test_generation_failure_completes_unknown_without_model_approval(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.plan.return_value = [{"text": "Explain this topic.", "action": "answer"}]
        calls.reasoner.check = AsyncMock(return_value={"approved": False})

        async def broken(*args, **kwargs):
            raise ValueError("provider failed")
            yield

        calls.reasoner.answer_stream = broken
        await service.submit(sid, "Explain this topic.")
        await drain(service)
        state = service.get(sid)
        answer = state["answers"][-1]
        assert answer["text"] == "I don't know."
        assert answer["status"] == "completed"
        assert not answer.get("error")
        assert state["questions"][0]["status"] == "answered"
        assert state["questions"][0]["reply_failure"]["error"] == "ValueError"
        calls.reasoner.check.assert_not_awaited()

    asyncio.run(scenario())
