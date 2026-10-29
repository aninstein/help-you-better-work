"""Current Wiki/Hinter data must survive routing and reach the answer composer."""

import asyncio
import json
from unittest.mock import AsyncMock

from app.agent.call_policy import CallPolicy
from app.agent.call_reasoner import CallReasoner
from app.agent.provided_evidence import evidence_material, routing_constraints

QUESTION = "I'd like to know TikTok's revenue, annual tax payments, and daily and monthly users."
HINT = "TikTok日活是1.2亿人次，月活是23亿人；年度收入统计口径待确认。"


def test_old_ai_quote_in_guidance_context_is_not_supplied_evidence(tmp_path):
    from test_live_tools import setup

    async def scenario():
        _, calls, _ = setup(tmp_path)
        old = "The only figure I found is an unconfirmed estimate."
        constraints = ("[hint-1 / instruction / session] " + HINT
                       + "\nHINTER INPUT WITH CONVERSATION CONTEXT:\n"
                       + json.dumps({"recent_messages": [{"speaker": "ai", "text": old}]}))
        calls.reasoner.ask = AsyncMock(return_value={"questions": [{
            "text": QUESTION, "action": "answer", "data_scope": "public",
            "evidence_status": "sufficient", "evidence_quotes": [old],
        }]})
        result = await CallReasoner.plan(calls.reasoner, QUESTION, "", constraints, [])
        assert result[0]["evidence_status"] == "missing"
        assert old not in evidence_material("", constraints)
        assert old not in routing_constraints(constraints)
        assert HINT in calls.reasoner.ask.call_args.args[1]["supplied_evidence"]
        assert calls.reasoner.ask.call_args.kwargs["review"] is True

    asyncio.run(scenario())


def test_partial_human_values_preserve_facts_and_search_missing_public_data(tmp_path):
    from test_live_tools import setup

    async def scenario():
        _, calls, _ = setup(tmp_path)
        constraints = "[hint-1 / instruction / session] " + HINT
        calls.reasoner.ask = AsyncMock(return_value={"questions": [{
            "text": QUESTION, "action": "lookup", "data_scope": "public",
            "evidence_status": "partial", "evidence_quotes": ["日活是1.2亿人次", "月活是23亿人"],
            "evidence_gap": "Tax and annual revenue are not supplied unambiguously.",
        }]})
        result = (await CallReasoner.plan(calls.reasoner, QUESTION, "", constraints, []))[0]
        assert result["evidence_source"] == "provided"
        assert result["evidence_status"] == "partial"
        assert result["web_query"] == QUESTION and not result["use_tools"]
        assert CallPolicy().decide(result, constraints) == ("lookup", "public_evidence_incomplete")

    asyncio.run(scenario())


def test_partial_wiki_recall_reaches_generation_context(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.plan.return_value = [{
            "text": QUESTION, "action": "answer", "data_scope": "public",
            "evidence_status": "partial", "evidence_source": "provided",
            "evidence_quotes": ["日活是1.2亿人次", "月活是23亿人"],
            "evidence_gap": "Annual tax payments are missing.", "uses_wiki_facts": True,
        }]

        async def stream(*args, context=None, **kwargs):
            assert context["retrieval_mode"] == "mixed"
            assert context["evidence_quotes"] == ["日活是1.2亿人次", "月活是23亿人"]
            assert context["tool_evidence"]["name"] == "web_search"
            yield "The supplied MAU is 2.3 billion. Annual tax payments are still missing."

        calls.reasoner.answer_stream = stream
        await service.submit(sid, QUESTION)
        await drain(service)
        state = service.get(sid)
        assert state["questions"][0]["status"] == "answered"
        assert state["answers"][-1]["text"] != "I don't know."
        calls.search.lookup.assert_awaited_once_with(QUESTION)

    asyncio.run(scenario())


def test_explicit_public_verification_still_searches_supplied_data():
    result = {
        "text": "Search official sources to verify TikTok's DAU.", "action": "lookup",
        "data_scope": "public", "evidence_status": "partial",
        "evidence_source": "provided", "evidence_quotes": [HINT],
    }
    assert CallPolicy().decide(result, "")[0] == "lookup"


def test_public_partial_lookup_obeys_internal_prohibition_and_search_boundaries():
    from app.agent.call_search import CallSearch

    base = {"text": QUESTION, "category": "data_lookup", "action": "answer",
            "data_scope": "public", "evidence_status": "partial",
            "evidence_source": "provided", "evidence_quotes": [HINT]}
    policy = CallPolicy()
    for name in ["TikTok", "Nimbus", "Orchid"]:
        public = {**base, "text": QUESTION.replace("TikTok", name)}
        assert policy.decide(public, "")[0] == "lookup"
    assert not CallSearch.needs_public_completion({**base, "data_scope": "internal"})
    assert not CallSearch.needs_public_completion({**base, "text": "Don't search. " + QUESTION})
    assert policy.decide({**base, "evidence_status": "sufficient"}, "")[0] == "answer"
    assert policy.decide({**base, "matched_rule_ids": ["r"]},
                         "[r / prohibition / session] Topic is prohibited.")[0] == "block"
    assert policy.decide({**base, "matched_rule_ids": ["r"]},
                         "[r / require_human / session] Confirm first.")[0] == "await_human"


def test_failed_public_completion_preserves_supplied_facts(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.reasoner.plan.return_value = [{
            "text": QUESTION, "action": "answer", "data_scope": "public",
            "category": "data_lookup", "evidence_status": "partial",
            "evidence_source": "provided", "evidence_quotes": [HINT],
            "evidence_gap": "Revenue and tax remain missing.",
        }]
        calls.search.lookup = AsyncMock(side_effect=ValueError("provider unavailable"))

        async def stream(*args, context=None, **kwargs):
            assert context["retrieval_mode"] == "provided"
            assert context["evidence_quotes"] == [HINT]
            yield "The supplied MAU is 2.3 billion; revenue and tax could not be verified."

        calls.reasoner.answer_stream = stream
        await service.submit(sid, QUESTION)
        await drain(service)
        state = service.get(sid)
        calls.search.lookup.assert_awaited_once_with(QUESTION)
        assert state["questions"][0]["work"]["state"] == "failed"
        assert "2.3 billion" in state["answers"][-1]["text"]
        assert state["answers"][-1]["text"] != "I don't know."

    asyncio.run(scenario())


def test_bound_partial_correction_does_not_disable_missing_public_lookup(tmp_path):
    from test_live_tools import drain, setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        partial = {"text": QUESTION, "action": "answer", "data_scope": "public",
                   "category": "data_lookup", "evidence_status": "partial",
                   "evidence_source": "provided", "evidence_quotes": [HINT]}
        calls.reasoner.plan.return_value = [{**partial, "evidence_status": "sufficient"}]

        async def stream(*args, context=None, **kwargs):
            yield "The supplied MAU is 2.3 billion."

        calls.reasoner.answer_stream = stream
        await service.submit(sid, QUESTION)
        await drain(service)
        qid = service.get(sid)["questions"][0]["id"]
        await service.guidance.submit(sid, {"text": HINT, "kind": "authorized_fact",
                                           "question_id": qid}, trigger_reply=False)
        await service.replies.correct(sid, qid, plan=partial, confirm=False)
        await drain(service)
        calls.search.lookup.assert_awaited_once_with(QUESTION)
        assert service.get(sid)["questions"][0]["retrieval_mode"] == "mixed"

    asyncio.run(scenario())


def test_topic_after_twentieth_document_is_recalled(tmp_path):
    from app.adapters.wiki import WikiStore

    wiki = WikiStore(tmp_path / "wiki")
    for i in range(25):
        wiki.write("talker", f"notes/a{i:02}.md", f"# Unrelated topic {i}\nOther facts.")
    wiki.write("talker", "notes/z-nimbus.md", "# Nimbus pilot\nNimbus improved throughput by 30%.")
    result = wiki.bundle("talker", "sid", include_history=False, query="Nimbus pilot improvement")
    assert "Nimbus improved throughput by 30%" in result


def test_completed_auto_reply_updates_memory_once_and_skips_unknown(tmp_path):
    from test_live_tools import setup

    async def scenario():
        service, _, sid = setup(tmp_path)
        state = service.get(sid)
        state["questions"].append({"id": "q", "text": "Nimbus pilot result?", "decision": "answer"})
        answer = {
            "id": "a", "status": "completed", "question_ids": ["q"],
            "text": "Nimbus improved throughput by 30%.", "reply_kind": "answer",
            "published_at": state["created_at"],
        }
        state["answers"].append(answer)
        service.store.save(state)
        await service.remember_completed_reply(sid, "a")
        await service.remember_completed_reply(sid, "a")
        assert service.organize_wiki.await_count == 2  # One update per role, not per ACK.
        assert "Nimbus improved throughput by 30%" in service.organize_wiki.call_args.args[2]
        state = service.get(sid)
        state["answers"].append({**answer, "id": "unknown", "text": "I don't know."})
        service.store.save(state)
        await service.remember_completed_reply(sid, "unknown")
        assert service.organize_wiki.await_count == 2

    asyncio.run(scenario())
