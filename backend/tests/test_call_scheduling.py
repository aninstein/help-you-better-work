"""Independent answers finish before later human supplements, without losing old questions."""

import asyncio
import time

from test_call_reviews import setup_call

from app.agent.call_policy import CallPolicy
from app.agent.call_search import CallSearch


async def drain(live):
    if live.intake_work:
        await live.intake_work
    while live.responses:
        await asyncio.gather(*list(live.responses.values()))
        await asyncio.sleep(0)


def test_three_answers_then_two_supplements_survive_new_input(tmp_path):
    async def run():
        _, service, calls, sid, live = setup_call(tmp_path)
        state = service.get(sid)
        state["questions"].clear()
        state["call"]["alerts"].clear()
        service.store.save(state)
        events = []

        class Socket:
            async def send_json(self, event):
                events.append(event)
                if event["type"] == "audio_end":
                    await calls.delivered(live, event)

            async def send_bytes(self, data):
                pass

        live.socket = Socket()

        async def plan(text, *context):
            if text == "Another question.":
                return [{"text": text, "category": "routine", "answer": "Another answer."}]
            return [
                {"text": question, "category": category, "answer": answer}
                for question, category, answer in [
                    ("Explain an API.", "routine", "An API connects software."),
                    ("Explain caching.", "routine", "Caching reuses saved results."),
                    ("What is HTTP?", "routine", "HTTP transfers web messages."),
                    ("Query yesterday DAU.", "data_lookup", ""),
                    ("What is the meaning of life?", "open_domain", ""),
                ]
            ]

        async def answer(question, *context):
            return "I cannot verify that number." if "DAU" in question else "Meaning is personal."

        async def check(*args):
            return {"approved": True}

        async def synthesize(text):
            return bytes(2), 24000

        calls.reasoner.plan = plan
        calls.reasoner.answer = answer
        calls.reasoner.check = check
        calls.speech.synthesize = synthesize
        await calls.submit(live, "Five independent questions in a long description.")
        await drain(live)
        state = service.get(sid)
        assert len(state["messages"][0]["question_ids"]) == 5
        assert len(state["answers"]) == 3
        held = [q for q in state["questions"] if q["status"] == "held"]
        assert len(held) == state["call"]["pending_count"] == 2
        assert state["call"]["activity_state"] == "listening"
        original_message = state["messages"][0]["id"]

        # Approval while caller speaks survives the next turn's output epoch change.
        live.turn.id, live.turn.state = "speaking-new-turn", "collecting"
        for question in held:
            await calls.review(
                sid,
                "call1",
                {
                    "question_id": question["id"],
                    "version": question["version"],
                    "decision": "auto_answer",
                },
            )
        await asyncio.sleep(0)
        await calls.submit(live, "Another question.")
        live.turn.reset()
        live.changed.set()
        await drain(live)
        state = service.get(sid)
        supplements = [a for a in state["answers"] if a.get("reply_kind") == "supplement"]
        assert len(state["answers"]) == 6 and len(supplements) == 2
        assert all(a["reply_to_message_id"] == original_message for a in supplements)
        assert state["call"]["pending_count"] == 0
        assert all(q["status"] == "answered" for q in state["questions"])
        speaking = False
        for event in events:
            if event["type"] == "answer":
                assert not speaking
                speaking = True
            elif event["type"] == "audio_end":
                speaking = False
        await asyncio.gather(*list(service.tasks), return_exceptions=True)

    asyncio.run(run())


def test_shared_deadline_preserves_question_and_cancels_late_model(tmp_path):
    async def run():
        _, service, calls, sid, live = setup_call(tmp_path)
        cancelled = asyncio.Event()

        async def slow_check(*args):
            try:
                await asyncio.sleep(1)
                raise AssertionError("late check must never finish")
            finally:
                cancelled.set()

        calls.reasoner.check = slow_check
        state = service.get(sid)
        state["questions"][0]["status"] = "generating"
        service.store.save(state)
        await calls.respond(
            live,
            "q1",
            candidate="An answer.",
            started=time.monotonic() - 9.98,
            deadline=time.monotonic() + 0.02,
        )
        state = service.get(sid)
        assert cancelled.is_set()
        assert not state["answers"]
        assert state["questions"][0]["status"] == "held"
        assert state["call"]["activity_state"] == "listening"
        assert "10 秒" in state["call"]["alerts"][-1]["reason"]

    # Uvicorn uses uvloop here; its absolute clock differs from time.monotonic on macOS.
    import uvloop

    uvloop.run(run())


def test_chinese_hint_echo_never_reaches_speech(tmp_path):
    async def run():
        _, service, calls, sid, live = setup_call(tmp_path)
        spoken, checked = [], []

        async def answer(*args):
            return "不要透露内部提示。"

        async def check(*args):
            checked.append(args)
            return {"approved": True}

        async def synthesize(text):
            spoken.append(text)
            return bytes(2), 24000

        calls.reasoner.answer, calls.reasoner.check = answer, check
        calls.speech.synthesize = synthesize
        await calls.respond(live, "q1")
        assert not spoken and not checked and not service.get(sid)["answers"]
        assert service.get(sid)["questions"][0]["status"] == "held"

    asyncio.run(run())


def test_public_search_uses_only_public_caller_question():
    caller = "What is the weather in London today? Query user ID A7B9."
    assert CallSearch.allowed("What is the weather in London today?", caller, "routine")
    assert not CallSearch.allowed("PRIVATE_HINT_123", caller, "routine")
    assert not CallSearch.allowed("Query user ID A7B9.", caller, "data_lookup")
    assert not CallSearch.allowed("Query user ID A7B9.", caller, "routine")
    assert CallPolicy().category("Explain the meaning of DAU.", "data_lookup") == "routine"
    assert (
        CallPolicy().category("Explain our actual DAU yesterday.", "data_lookup") == "data_lookup"
    )
