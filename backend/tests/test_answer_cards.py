"""One caller input owns one public answer; attempts remain private audit data."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.application.answer_cards import AnswerCards, merge_parts, text_ready, spoken_parts


def part(key, value, status="known"):
    return {"id": key, "question_id": "q", "question": key,
            "answer": value, "status": status}


def fixture():
    questions = [{"id": f"q{i}", "text": label, "status": "answered", "version": 1,
                  "source_message_ids": ["caller"], "decision": "answer"}
                 for i, label in enumerate(["Revenue?", "DAU?", "Tax?"])]
    answers = [{"id": f"a{i}", "question_ids": [f"q{i}"], "text": value,
                "status": "completed", "public_stream": True, "reply_kind": "answer",
                "created_at": "2026-10-23T00:00:01+00:00", "parts_status": "ready",
                "answer_parts": [part(f"q{i}:field", value, "unknown" if i == 2 else "known")]}
               for i, value in enumerate(["Revenue is 10.", "DAU is 20.", "I don't know."])]
    for i, answer in enumerate(answers):
        answer["answer_parts"][0]["question_id"] = f"q{i}"
        questions[i]["answer_parts"] = deepcopy(answer["answer_parts"])
    state = {"id": "session", "questions": questions, "answers": answers,
             "messages": [{"id": "caller", "type": "talker", "text": "Revenue, DAU and tax?",
                           "created_at": "2026-10-23T00:00:00+00:00"}]}
    return state


def project(state):
    service = type("Service", (), {})()
    cards = AnswerCards(service)
    result = {"messages": deepcopy(state["messages"]) + [
        {**deepcopy(a), "type": "ai"} for a in state["answers"]],
        "answers": deepcopy(state["answers"])}
    cards.project(state, result)
    return result


def test_all_subquestion_outcomes_form_one_bubble_with_unknown_part():
    state = fixture()
    state["answers"].insert(0, {**state["answers"][0], "id": "ack",
                                "reply_kind": "acknowledgement", "text": "Let me check that."})
    result = project(state)
    messages = [m for m in result["messages"] if m["type"] == "ai"]
    assert len(messages) == len(result["answers"]) == 1
    assert messages[0]["id"] == "reply-caller"
    assert len(messages[0]["answer_parts"]) == 3
    assert messages[0]["answer_parts"][-1]["status"] == "unknown"
    assert "Let me check" not in messages[0]["text"]
    assert len(state["answers"]) == 4  # Attempts were not deleted.


def test_initial_bubble_waits_for_every_question_outcome():
    state = fixture()
    state["answers"][-1].update(status="streaming", parts_status="pending")
    state["questions"][-1]["status"] = "generating"
    result = project(state)
    assert not [m for m in result["messages"] if m["type"] == "ai"]
    assert result["messages"][0]["reply_summary"] == {
        "state": "collecting", "completed": 2, "total": 3,
    }


def test_complete_text_is_visible_before_audio_or_formatter_finishes():
    state = fixture()
    last = state["answers"][-1]
    last.pop("answer_parts")
    last.update(status="streaming", parts_status="pending", text_complete=True,
                text_done=True, audio_done=False, text="Tax is 30.")
    state["questions"][-1]["status"] = "generating"
    result = project(state)
    assert len(result["answers"]) == 1
    assert result["answers"][0]["id"] == "reply-caller"
    assert "Tax is 30." in result["answers"][0]["text"]


@pytest.mark.parametrize("legacy", [False, True])
def test_audio_interruption_keeps_fully_generated_text_visible(legacy):
    state = fixture()
    answer = state["answers"][-1]
    answer.pop("answer_parts")
    answer.update(status="interrupted", parts_status="pending", text_done=True,
                  audio_done=False, text="Tax is 30.", delivery="interrupted")
    if legacy:
        answer["metrics"] = {"model_done_ms": 8481}
    else:
        answer["text_complete"] = True
    state["questions"][-1]["status"] = "interrupted"
    result = project(state)
    assert len(result["answers"]) == 1
    assert "Tax is 30." in result["answers"][0]["text"]
    assert result["answers"][0]["id"] == "reply-caller"


def test_text_done_on_cancel_does_not_claim_complete_generation():
    interrupted = {"status": "interrupted", "text_done": True,
                   "text": "Tax is", "metrics": {"model_done_ms": None}}
    assert not text_ready(interrupted)
    assert not text_ready({**interrupted, "text_complete": False})
    assert not text_ready({**interrupted, "text_complete": True, "status": "withdrawn"})
    assert not text_ready({**interrupted, "text_complete": True, "reply_kind": "progress"})


def test_audio_playing_does_not_leave_structured_card_updating():
    state = fixture()
    state["answers"][-1].update(status="streaming", text_complete=True, audio_done=False)
    state["questions"][-1]["status"] = "generating"
    card = project(state)["answers"][0]
    assert not card["updating"]


@pytest.mark.parametrize("delivery_status", ["streaming", "interrupted"])
def test_formatter_accepts_complete_text_independent_of_audio(tmp_path, delivery_status):
    from test_live_tools import setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        state = service.get(sid)
        data = fixture()
        question = data["questions"][0]
        question.update(category="data_lookup", status="generating")
        state["questions"] = [question]
        state["answers"] = [{**data["answers"][0], "status": delivery_status,
                              "text_complete": True, "audio_done": False,
                              "parts_status": "pending"}]
        service.store.save(state)
        calls.reasoner.structure_reply = AsyncMock(return_value=[part("q0:field", "Revenue is 10.")])
        await service.answer_cards.format_answer(sid, "a0")
        saved = service.get(sid)["answers"][0]
        assert saved["parts_status"] == "ready"
        assert saved["status"] == delivery_status and not saved["audio_done"]

    asyncio.run(scenario())


def test_correction_replaces_unknown_in_same_bubble_and_preserves_other_answers():
    state = fixture()
    before = project(state)["answers"][0]
    state["answers"].append({**deepcopy(state["answers"][-1]), "id": "tax-correction",
                             "text": "Tax is 30.", "answer_parts": [part("q2:field", "Tax is 30.")],
                             "reply_kind": "supplement"})
    after = project(state)["answers"][0]
    assert after["id"] == before["id"]
    assert after["text"] != before["text"] and "Tax is 30." in after["text"]
    assert "Revenue is 10." in after["text"] and "DAU is 20." in after["text"]
    assert len(after["answer_parts"]) == 3
    assert after["tts_audio"]["spoken_text"].endswith("q2:field Tax is 30.")


def test_pending_correction_keeps_complete_previous_version_until_ready():
    state = fixture()
    before = project(state)["answers"][0]
    state["answers"].append({**deepcopy(state["answers"][-1]), "id": "pending-correction",
                             "status": "streaming", "parts_status": "pending", "text": ""})
    state["questions"][-1]["status"] = "generating"
    after = project(state)["answers"][0]
    assert after["id"] == before["id"] and after["text"] == before["text"]
    assert after["updating"]


def test_unknown_cannot_erase_known_without_explicit_retraction():
    old = [part("revenue", "Revenue is 10."), part("tax", "I don't know.", "unknown")]
    updated = merge_parts(old, [part("revenue", "I don't know.", "unknown"),
                                part("tax", "Tax is 30.")])
    assert updated[0]["answer"] == "Revenue is 10."
    assert updated[1]["answer"] == "Tax is 30." and updated[1]["updated"]
    retracted = merge_parts(updated, [{**part("revenue", "I don't know.", "unknown"),
                                      "replace": True}])
    assert retracted[0]["status"] == "unknown"


def test_withdrawn_latest_part_never_resurrects_prior_value():
    state = fixture()
    state["answers"].append({**deepcopy(state["answers"][0]), "id": "withdrawn-revenue",
                             "status": "withdrawn"})
    result = project(state)["answers"][0]
    assert "Revenue is 10." not in result["text"]
    assert "DAU is 20." in result["text"]


def test_formatter_cannot_invent_value_not_in_validated_answer(tmp_path):
    from test_live_tools import setup

    from app.agent.call_reasoner import CallReasoner

    async def scenario():
        _, calls, _ = setup(tmp_path)
        calls.reasoner.ask = AsyncMock(return_value={"parts": [
            {"question": "DAU?", "answer": "DAU is 999.", "status": "known"},
            {"question": "Tax?", "answer": "I don't know.", "status": "unknown"},
        ]})
        with pytest.raises(ValueError, match="not_quoted"):
            await CallReasoner.structure_reply(calls.reasoner, "DAU and tax?", "DAU is 20.", "q", [])

    asyncio.run(scenario())


def test_late_formatter_cannot_overwrite_a_newer_correction(tmp_path):
    from test_live_tools import setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        state = service.get(sid)
        data = fixture()
        state["questions"] = [data["questions"][0]]
        state["questions"][0]["subrequests"] = ["Revenue", "Tax"]
        old = {**data["answers"][0], "id": "old", "parts_status": "pending"}
        state["answers"] = [old]
        service.store.save(state)
        entered, release = asyncio.Event(), asyncio.Event()

        async def structure(*args, **kwargs):
            entered.set()
            await release.wait()
            return [part("q0:field", "Old stale value.")]

        calls.reasoner.structure_reply = AsyncMock(side_effect=structure)
        pending = asyncio.create_task(service.answer_cards.format_answer(sid, "old"))
        await entered.wait()
        current = service.get(sid)
        current["answers"].append({**old, "id": "new", "parts_status": "ready",
                                    "answer_parts": [part("q0:field", "Corrected value.")]})
        current["questions"][0]["answer_parts"] = [part("q0:field", "Corrected value.")]
        service.store.save(current)
        release.set()
        await pending
        assert service.get(sid)["questions"][0]["answer_parts"][0]["answer"] == "Corrected value."

    asyncio.run(scenario())


def test_single_weather_keeps_full_original_text_even_with_legacy_short_parts():
    state = fixture()
    state["questions"] = [state["questions"][0]]
    state["questions"][0].update(category="data_lookup", subrequests=["Current weather"])
    answer = state["answers"][0]
    full = "It is 20 degrees. Rain will continue tonight. Source observation was at 10:30 p.m."
    answer.update(text=full, answer_parts=[part("q0:field", "It is 20 degrees.")])
    state["answers"] = [answer]
    card = project(state)["answers"][0]
    assert card["text"] == full
    assert card["tts_audio"]["spoken_text"] == full


def test_single_streamed_sentence_is_visible_before_full_generation():
    state = fixture()
    state["questions"] = [state["questions"][0]]
    state["answers"] = [{**state["answers"][0], "text": "It is 20 degrees.",
                         "status": "streaming", "text_complete": False,
                         "parts_status": "pending", "audio_done": False}]
    card = project(state)["answers"][0]
    assert card["text"] == "It is 20 degrees."
    assert card["status"] == "streaming" and not card["text_done"]


def test_single_lookup_skips_secondary_model_and_keeps_full_text(tmp_path):
    from test_live_tools import setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        state = service.get(sid)
        data = fixture()
        state["questions"] = [data["questions"][0]]
        state["questions"][0].update(category="data_lookup", subrequests=["Weather"])
        full = "It is 20 degrees. Rain is likely. The observation was at 10:30."
        state["answers"] = [{**data["answers"][0], "text": full, "parts_status": "pending"}]
        service.store.save(state)
        await service.answer_cards.format_answer(sid, "a0")
        calls.reasoner.structure_reply.assert_not_awaited()
        assert service.get(sid)["answers"][0]["answer_parts"][0]["answer"] == full

    asyncio.run(scenario())


def test_one_bad_quote_does_not_discard_other_valid_updated_parts(tmp_path):
    from test_live_tools import setup
    from app.agent.call_reasoner import CallReasoner

    async def scenario():
        _, calls, _ = setup(tmp_path)
        previous = [part("revenue", "Revenue is 10."), part("tax", "I don't know.", "unknown")]
        calls.reasoner.ask = AsyncMock(return_value={"parts": [
            {"id": "revenue", "question": "revenue", "answer": "Revenue is 999.", "status": "known"},
            {"id": "", "question": "tax", "answer": "Tax is 30.", "status": "known"},
        ]})
        incoming = await CallReasoner.structure_reply(calls.reasoner, "Revenue and tax?",
                                                      "Revenue is 10. Tax is 30.", "q", previous)
        merged = merge_parts(previous, incoming)
        assert merged[0]["answer"] == "Revenue is 10."
        assert merged[1]["answer"] == "Tax is 30."

    asyncio.run(scenario())


def test_sentence_selection_recovers_quote_copying_error_without_new_facts(tmp_path):
    from test_live_tools import setup
    from app.agent.call_reasoner import CallReasoner

    async def scenario():
        _, calls, _ = setup(tmp_path)
        calls.reasoner.ask = AsyncMock(return_value={"parts": [
            {"question": "tax", "answer": "Tax is approximately 999.", "status": "known",
             "sentence_indices": [1]},
        ]})
        parts = await CallReasoner.structure_reply(calls.reasoner, "Tax?",
                                                   "Revenue is 10. Tax is 30.", "q", [])
        assert parts[0]["answer"] == "Tax is 30."

    asyncio.run(scenario())


def test_group_update_speaks_only_changed_items_with_a_natural_intro():
    before = [part("revenue", "Revenue is 10."), part("tax", "I don't know.", "unknown")]
    after = merge_parts(before, [part("tax", "Tax is 30.")])
    speech = spoken_parts(after, before)
    assert speech.startswith("Oh, I've updated")
    assert "tax: Tax is 30." in speech and "Revenue is 10." not in speech
    assert spoken_parts(after, after) == ""
    assert not spoken_parts(before, []).startswith("Oh")


def test_group_card_is_updated_before_any_correction_audio_is_queued(tmp_path):
    from test_live_tools import setup
    from app.application.responses import ReplyRun

    async def scenario():
        service, calls, sid = setup(tmp_path)
        before = [part("revenue", "Revenue is 10."), part("tax", "I don't know.", "unknown")]
        state = service.get(sid)
        state["messages"] = [{"id": "caller", "type": "talker", "text": "Revenue and tax?"},
                             {"id": "update", "type": "ai", "text": "Tax is 30."}]
        state["questions"] = [{"id": "q", "text": "Revenue and tax?", "version": 2,
                                 "status": "generating", "decision": "answer",
                                 "subrequests": ["revenue", "tax"], "answer_parts": before,
                                 "source_message_ids": ["caller"]}]
        state["answers"] = [{"id": "update", "question_ids": ["q"], "public_stream": True,
                              "text": "Tax is 30.", "status": "streaming", "reply_kind": "supplement",
                              "parts_status": "pending", "text_complete": True, "segments": []}]
        service.store.save(state)
        calls.reasoner.structure_reply = AsyncMock(return_value=[part("tax", "Tax is 30.")])
        run = ReplyRun(sid, "q", "update", 1, 1, defer_audio=True, previous_parts=before)
        service.replies.ensure = lambda _: None
        queued = []

        async def enqueue(_, item):
            current = service.get(sid)
            card = service.answer_cards.current_card(current, current["questions"][0])
            assert "Tax is 30." in card["text"]
            queued.append(item[1])

        service.replies.enqueue = enqueue
        await service.replies.prepare_group_audio(run)
        assert "Oh, I've updated" in " ".join(queued)
        assert "Tax is 30." in " ".join(queued) and "Revenue is 10." not in " ".join(queued)

    asyncio.run(scenario())


def test_part_hint_binds_a_bare_value_to_the_named_subquestion(tmp_path):
    from test_live_tools import setup

    async def scenario():
        service, calls, sid = setup(tmp_path)
        calls.schedule_retrospection = lambda *args: None
        state = service.get(sid)
        state["questions"] = [{"id": "q", "text": "Revenue and tax?", "version": 1,
                                 "status": "answered", "answer_parts": [part("tax", "I don't know.", "unknown")]}]
        service.store.save(state)
        await service.guidance.submit(sid, {"text": "30 dollars", "question_id": "q", "part_id": "tax"}, trigger_reply=False)
        record = service.guidance.records(service.get(sid))[0]
        assert record["text"] == "30 dollars"
        assert record["part_id"] == "tax"
        assert record["binding"]["target_part"]["question"] == "tax"

    asyncio.run(scenario())
