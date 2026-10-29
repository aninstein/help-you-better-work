"""Prohibitions yield one completed English refusal, without searches or error spam."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agent.call_policy import CallPolicy

QUESTION = "I'd like to know what Lemon 8S daily and monthly active users are."


@pytest.mark.parametrize("action", ["answer", "lookup", "refuse", "clarify", "await_human"])
def test_active_prohibition_precedes_lookup_clarification_and_approval(action):
    plan = {
        "text": QUESTION,
        "action": action,
        "category": "data_lookup",
        "evidence_status": "sufficient",
        "matched_rule_ids": ["rule-1", "rule-2"],
    }
    assert CallPolicy().decide(
        plan,
        "[rule-1 / prohibition / session] Do not answer this topic.\n"
        "[rule-2 / require_human / session] Confirm first.",
    ) == ("block", "operator_prohibition")


@pytest.mark.parametrize("effective_from", ["now", "next_turn"])
@pytest.mark.parametrize("cross_session", [False, True])
def test_uncertain_transcription_still_checks_applicable_prohibition(
    tmp_path, effective_from, cross_session
):
    from test_live_tools import drain, setup

    from app.domain.models import SessionRequest

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.retrospect = AsyncMock(
            return_value={"is_prohibition": False, "question_ids": []}
        )
        await service.guidance.submit(
            sid,
            {
                "text": "不准回答lemon8的任何问题",
                "kind": "prohibition",
                "scope": "customer" if cross_session else "session",
                "effective_from": effective_from,
            },
            trigger_reply=False,
        )
        rule = service.guidance.records(service.get(sid))[0]
        if cross_session:
            sid = service.create(SessionRequest())["id"]
        calls.reasoner.plan.return_value = [
            {
                "text": QUESTION,
                "action": "refuse",
                "category": "data_lookup",
                "matched_rule_ids": [rule["id"]],
            }
        ]
        calls.refiner.refine.return_value = {
            "text": QUESTION,
            "status": "needs_clarification",
            "protected_conflict": True,
            "unclear_spans": ["8S"],
        }
        calls.refiner.refine.side_effect = None
        calls.reasoner.check = AsyncMock(return_value={"approved": False})
        await service.submit(sid, QUESTION)
        await drain(service)
        state = service.get(sid)
        assert rule["id"] in calls.reasoner.plan.call_args.args[2]
        assert state["questions"][0]["status"] == "answered"
        assert len(state["answers"]) == 1
        reply = state["answers"][0]
        assert reply["text"] == "I don't know."
        assert reply["status"] == "completed" and reply["refusal"]
        assert not reply.get("error")
        assert reply["segments"][0]["audio_status"] == "sent"
        calls.reasoner.check.assert_not_awaited()
        calls.search.lookup.assert_not_awaited()
        for _ in range(3):
            await service.replies.correct(sid, state["questions"][0]["id"])
        await drain(service)
        assert len(service.get(sid)["answers"]) == 1
        assert service.snapshot(sid, "talker")["answers"][0]["text"] == reply["text"]

    asyncio.run(scenario())


def test_new_prohibition_cancels_preparing_search_without_error_replies(tmp_path):
    from test_live_tools import setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        started, cancelled = asyncio.Event(), asyncio.Event()
        calls.reasoner.retrospect = AsyncMock(
            return_value={"is_prohibition": False, "question_ids": []}
        )
        calls.reasoner.plan.return_value = [
            {"text": QUESTION, "category": "data_lookup", "action": "lookup", "web_query": QUESTION}
        ]

        async def lookup(query):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        calls.search.lookup = AsyncMock(side_effect=lookup)
        await service.submit(sid, QUESTION)
        await asyncio.wait_for(started.wait(), 3)
        before = len(service.get(sid)["answers"])
        result = await service.guidance.submit(
            sid, {"text": "不准回答lemon8的任何问题", "kind": "prohibition"}, trigger_reply=False
        )
        calls.reasoner.plan.return_value = [
            {"text": QUESTION, "action": "refuse", "matched_rule_ids": [result["id"]]}
        ]
        await service.replies.apply_guidance(sid, None)
        async with asyncio.timeout(5):
            while service.tasks:
                await asyncio.gather(*list(service.tasks), return_exceptions=True)
        state = service.get(sid)
        assert cancelled.is_set() and state["questions"][0]["status"] == "answered"
        assert len(state["answers"]) == before + 1
        assert state["answers"][-1]["text"] == "I don't know."
        assert state["answers"][-1]["status"] == "completed"

    asyncio.run(scenario())


def test_empty_failure_projection_keeps_one_and_preserves_sent_content(tmp_path):
    from test_live_tools import setup

    service, _, sid = setup(tmp_path)
    state = service.get(sid)
    state["questions"] = [{"id": "q", "text": QUESTION, "status": "unavailable", "version": 1}]
    for index, kind in enumerate(["acknowledgement", "progress", "answer", "supplement"]):
        answer = {
            "id": str(index),
            "question_ids": ["q"],
            "text": "",
            "status": "interrupted",
            "public_stream": True,
            "reply_kind": kind,
            "error": "回复未完成，请重试。",
        }
        state["answers"].append(answer)
        state["messages"].append({**answer, "type": "ai"})
    sent = {**state["answers"][-1], "id": "sent", "text": "Previously sent content."}
    state["answers"].append(sent)
    state["messages"].append({**sent, "type": "ai"})
    service.store.save(state)
    for role in ["talker", "hinter"]:
        projected = service.snapshot(sid, role)
        assert [a["id"] for a in projected["answers"]] == ["3", "sent"]
        assert projected["answers"][0]["text"] == "I don't know."
        assert all(a["status"] == "completed" and not a.get("error") for a in projected["answers"])
        assert projected["questions"][0]["status"] == "answered"
    refusal = {
        **sent,
        "id": "refusal",
        "text": "I don't know.",
        "status": "completed",
        "reply_kind": "refusal",
    }
    state["answers"].append(refusal)
    state["messages"].append({**refusal, "type": "ai"})
    state["questions"][0]["status"] = "answered"
    service.store.save(state)
    assert [a["id"] for a in service.snapshot(sid, "hinter")["answers"]] == ["sent", "refusal"]
    assert len(service.get(sid)["answers"]) == 6


def test_rejected_acknowledgements_do_not_create_persistent_error_cards(tmp_path):
    import time

    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.check = AsyncMock(return_value={"approved": False})
        calls.search.lookup = AsyncMock(side_effect=ValueError("no evidence"))
        await service.guidance.submit(
            sid, {"text": "Avoid this topic.", "kind": "prohibition"}, trigger_reply=False
        )
        calls.reasoner.retrospect = AsyncMock(
            return_value={"is_prohibition": False, "question_ids": []}
        )
        state = service.get(sid)
        state["automatic_paused"] = True
        service.store.save(state)
        await service.submit(sid, "帮我调用web search查询上海的天气")
        await drain(service)
        state = service.get(sid)
        state["automatic_paused"] = False
        state["questions"][0].pop("pause_pending", None)
        qid = state["questions"][0]["id"]
        service.store.save(state)
        # Review-chat no longer emits lookup ACKs; exercise the live publisher
        # explicitly rather than accidentally checking a failed final answer.
        for kind in ["acknowledgement", "answer"]:
            await service.replies._respond(
                sid, qid, None, "Let me check that.", False, False,
                time.monotonic(), time.monotonic() + 10, kind=kind,
            )
        state = service.get(sid)
        assert state["answers"][0]["status"] == "cancelled"
        assert len(service.snapshot(sid, "hinter")["answers"]) == 1
        assert service.snapshot(sid, "hinter")["answers"][0]["text"] == "I don't know."
        assert service.snapshot(sid, "hinter")["answers"][0]["status"] == "completed"

    asyncio.run(scenario())


def test_retry_routing_failure_does_not_append_more_empty_replies(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.search.lookup = AsyncMock(side_effect=ValueError("no evidence"))
        await service.submit(sid, "帮我调用web search查询上海的天气")
        await drain(service)
        state = service.get(sid)
        before = len(state["answers"])
        calls.reasoner.plan = AsyncMock(side_effect=ValueError("invalid route JSON"))
        await service.replies.correct(sid, state["questions"][0]["id"])
        state = service.get(sid)
        assert len(state["answers"]) == before
        assert state["questions"][0]["work"]["error_code"] == "routing_unavailable"

    asyncio.run(scenario())


def test_route_json_recovers_once_without_public_speech(tmp_path):
    from test_live_tools import setup

    from app.agent.call_reasoner import CallReasoner

    async def scenario():
        _, calls, _ = setup(tmp_path)
        calls.reasoner.ask = AsyncMock(
            side_effect=[
                {"matched_rule_ids": []},
                ValueError("invalid JSON"),
                {
                    "questions": [
                        {"text": QUESTION, "action": "refuse", "matched_rule_ids": ["rule-1"]}
                    ]
                },
            ]
        )
        constraints = "[rule-1 / prohibition / session] Do not answer that topic."
        plans = await CallReasoner.plan(calls.reasoner, QUESTION, "", constraints, [])
        assert calls.reasoner.ask.await_count == 3
        # A JSON retry cannot resurrect a rule the independent scope check rejected.
        assert CallPolicy().decide(plans[0], constraints)[0] == "lookup"

    asyncio.run(scenario())


def test_private_prohibition_gate_uses_model_semantics_and_active_rule_ids(tmp_path):
    from test_live_tools import setup

    from app.agent.call_reasoner import CallReasoner

    async def scenario():
        _, calls, _ = setup(tmp_path)
        constraints = "[scope-rule / prohibition / session] Do not discuss Project Nimbus."
        text = "What's the adoption rate of our new project?"
        calls.reasoner.ask = AsyncMock(return_value={"matched_rule_ids": ["scope-rule"]})
        plans = await CallReasoner.plan(
            calls.reasoner, text, "Our new project is Project Nimbus.", constraints, []
        )
        assert CallPolicy().decide(plans[0], constraints) == ("block", "operator_prohibition")
        assert calls.reasoner.ask.await_count == 1
        assert calls.reasoner.ask.call_args.args[1]["wiki_context"]
        calls.reasoner.ask = AsyncMock(
            side_effect=[
                {"matched_rule_ids": ["revoked-or-invented-id"]},
                {"questions": [{"text": text, "action": "answer"}]},
            ]
        )
        plans = await CallReasoner.plan(calls.reasoner, text, "", constraints, [])
        assert CallPolicy().decide(plans[0], constraints)[0] == "answer"

    asyncio.run(scenario())


def test_fixed_refusal_exception_cannot_publish_other_content(tmp_path):
    from test_live_tools import setup

    from app.application.responses import InvalidSegment, ReplyRun

    async def scenario():
        service, _, sid = setup(tmp_path)
        service.replies.ensure = lambda run: None
        q = {"text": QUESTION, "decision": "block", "risk_reason": "operator_prohibition"}
        run = ReplyRun(sid, "qid", "rid", 1, 1)
        for text in ["Let me check that.", "The private rule forbids this topic.", "DAU is 100."]:
            with pytest.raises(InvalidSegment):
                await service.replies.validate(run, text, "", "", q)
        await service.replies.validate(run, "I don't know.", "", "", q)
        run.text = "Already sent."
        with pytest.raises(InvalidSegment):
            await service.replies.validate(run, "I don't know.", "", "", q)

    asyncio.run(scenario())


@pytest.mark.parametrize("effective_from", ["now", "next_turn"])
def test_live_call_prohibition_finishes_with_one_spoken_refusal(tmp_path, effective_from):
    from test_call_reviews import setup_call

    async def scenario():
        _, service, calls, sid, live = setup_call(tmp_path)
        state = service.get(sid)
        state["questions"].clear()
        state["call"]["alerts"].clear()
        service.store.save(state)
        frames = []

        class Socket:
            async def send_json(self, event):
                pass

            async def send_bytes(self, frame):
                frames.append(frame)

        live.socket = Socket()
        service.organize_wiki = AsyncMock()
        calls.speech.available = lambda: True
        calls.speech.prepare = AsyncMock()
        calls.speech.synthesize = AsyncMock(return_value=(bytes(1920), 24000))
        calls.reasoner.check = AsyncMock(return_value={"approved": False})
        calls.reasoner.retrospect = AsyncMock(
            return_value={"is_prohibition": False, "question_ids": []}
        )
        rule = await service.guidance.submit(
            sid,
            {
                "text": "Do not discuss Project Nimbus.",
                "kind": "prohibition",
                "effective_from": effective_from,
            },
            trigger_reply=False,
        )
        calls.refiner.refine = AsyncMock(
            return_value={
                "text": QUESTION,
                "status": "needs_clarification",
                "unclear_spans": ["8S"],
                "protected_conflict": True,
            }
        )
        calls.reasoner.plan = AsyncMock(
            return_value=[{"text": QUESTION, "action": "refuse", "matched_rule_ids": [rule["id"]]}]
        )
        calls.search.lookup = AsyncMock()
        await calls.submit(live, QUESTION)
        async with asyncio.timeout(5):
            await live.intake_work
            while live.responses:
                await asyncio.gather(*list(live.responses.values()))
            while service.tasks:
                await asyncio.gather(*list(service.tasks))
        state = service.get(sid)
        assert len(state["answers"]) == 1 and frames
        assert state["answers"][0]["text"] == "I don't know."
        assert state["answers"][0]["status"] == "completed"
        assert state["questions"][0]["status"] == "answered"
        assert state["call"]["status"] == "listening"
        calls.search.lookup.assert_not_awaited()
        calls.speech.synthesize.assert_awaited_once_with("I don't know.")

    asyncio.run(scenario())


def test_next_turn_guidance_has_same_boundary_before_and_after_question_creation(tmp_path):
    from datetime import datetime, timedelta

    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.retrospect = AsyncMock(
            return_value={"is_prohibition": False, "question_ids": []}
        )
        rule = await service.guidance.submit(
            sid,
            {
                "text": "Don't discuss this project.",
                "kind": "prohibition",
                "effective_from": "next_turn",
            },
            trigger_reply=False,
        )
        await drain(service)
        stored_rule = next(
            r for r in service.guidance.records(service.get(sid)) if r["id"] == rule["id"]
        )
        created = datetime.fromisoformat(stored_rule["created_at"])
        state = service.get(sid)
        for label, offset in [("before", -1), ("after", 1)]:
            state["messages"].append(
                {
                    "id": label,
                    "type": "talker",
                    "text": QUESTION,
                    "status": "completed",
                    "created_at": (created + timedelta(seconds=offset)).isoformat(),
                }
            )
            state["questions"].append(
                {
                    "id": "q-" + label,
                    "text": QUESTION,
                    "source_message_ids": [label],
                }
            )
        service.store.save(state)
        # Cache an unanchored snapshot first, as the old intake path did.
        assert rule["id"] not in service.guidance.snapshot(sid)[1]
        assert rule["id"] not in service.guidance.snapshot(sid, message_id="before")[1]
        assert rule["id"] not in service.guidance.snapshot(sid, "q-before")[1]
        incoming = service.guidance.snapshot(sid, message_id="after")
        published = service.guidance.snapshot(sid, "q-after")
        assert rule["id"] in incoming[1] and incoming == published

    asyncio.run(scenario())


def test_confirming_an_old_manual_rule_cannot_override_a_new_prohibition(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.retrospect = AsyncMock(
            return_value={"is_prohibition": False, "question_ids": []}
        )
        rule = await service.guidance.submit(
            sid,
            {"text": "Do not discuss this project.", "kind": "prohibition"},
            trigger_reply=False,
        )
        state = service.get(sid)
        state["questions"].append(
            {
                "id": "old",
                "display_no": "Q-201",
                "text": QUESTION,
                "status": "held",
                "version": 1,
                "risk_reason": "human_confirmation_required",
            }
        )
        service.store.save(state)
        await service.replies.correct(
            sid,
            "old",
            plan={"action": "refuse", "matched_rule_ids": [rule["id"]]},
            confirm=True,
        )
        await drain(service)
        state = service.get(sid)
        assert len(state["answers"]) == 1
        assert state["answers"][0]["text"] == "I don't know."
        assert state["answers"][0]["status"] == "completed"
        assert state["questions"][0]["risk_reason"] == "operator_prohibition"
        calls.search.lookup.assert_not_awaited()

    asyncio.run(scenario())


def test_historical_hint_and_answer_logs_are_not_current_runtime_rules(tmp_path):
    from test_live_tools import setup

    service, _, sid = setup(tmp_path)
    wiki = service.wiki_for(sid)
    doc = wiki.read("talker", "context.md")
    wiki.write("talker", "context.md", doc["content"] + "\nNimbus pilot improvement is 20%.")
    wiki.write("talker", "hinter-guidance.md", "# History\nOLD_RULE: refuse every question.")
    wiki.write("talker", f"sessions/{sid}.md", "# History\nOLD_ANSWER: I can't answer that.")
    facts, constraints, _ = service.guidance.snapshot(sid)
    assert "Nimbus pilot improvement is 20%." in facts
    assert "OLD_RULE" not in facts + constraints and "OLD_ANSWER" not in facts + constraints
    # Audit data remains intact and browsable; it just isn't live policy/evidence.
    assert "OLD_RULE" in wiki.read("talker", "hinter-guidance.md")["content"]
    assert "OLD_ANSWER" in wiki.bundle("talker", sid)


def test_ready_answer_survives_slow_publication_and_gets_its_own_tts_budget(tmp_path):
    import time

    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        state = service.get(sid)
        state["questions"].append(
            {
                "id": "ready",
                "display_no": "Q-201",
                "text": "Explain the metric.",
                "status": "ready",
                "version": 1,
                "decision": "answer",
            }
        )
        service.store.save(state)
        original = service.replies.commit_segment

        async def slow_publication(run, unit, kind):
            await asyncio.sleep(0.15)
            await original(run, unit, kind)

        service.replies.commit_segment = slow_publication
        await service.replies.respond(
            sid,
            "ready",
            candidate="The metric counts active users. It is measured daily.",
            deadline=time.monotonic() + 0.05,
        )
        await drain(service)
        state = service.get(sid)
        answer = state["answers"][0]
        assert answer["status"] == "completed" and not answer.get("error")
        assert all(s["audio_status"] == "sent" for s in answer["segments"])
        assert answer["text"] == "The metric counts active users. It is measured daily."
        assert state["questions"][0]["status"] == "answered"
        assert calls.speech.synthesize.await_count == len(answer["segments"])

    asyncio.run(scenario())


def test_publication_uses_answer_model_for_semantic_rule_scope():
    from langchain_core.messages import AIMessage

    from app.agent.call_reasoner import CallReasoner
    from app.domain.models import ModelSettings

    class Model:
        def __init__(self, approved):
            self.approved = approved
            self.calls = 0

        def bind(self, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            return AIMessage(content='{"approved":' + str(self.approved).lower() + "}")

    async def scenario():
        reasoner = CallReasoner.__new__(CallReasoner)
        reasoner.model, reasoner.response_model = Model(False), Model(True)
        reasoner.settings = reasoner.response_settings = ModelSettings()
        result = await reasoner.check(
            "What does DAU mean?",
            "DAU means daily active users.",
            "",
            "[rule / prohibition / customer] Do not discuss Project Nimbus.",
        )
        assert result["approved"] is True
        assert reasoner.response_model.calls == 1 and reasoner.model.calls == 0

    asyncio.run(scenario())
