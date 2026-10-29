"""Small end-to-end regressions for automatic publication, guidance and incremental speech."""

import asyncio
import struct

from app.adapters.store import Store
from app.domain.models import SessionRequest
from app.main import create_app


def fixture(tmp_path):
    app = create_app(Store(tmp_path / "duet.db"))
    service, calls = app.state.service, app.state.calls
    sid = service.create(SessionRequest())["id"]

    async def prepare():
        pass

    async def synthesize(text):
        return bytes(1920), 24000

    async def plan(text, *args):
        return [{"text": text, "category": "routine", "action": "answer"}]

    async def stream(*args):
        yield "Here is the first answer. "
        yield "Here is the second sentence."

    async def check(*args):
        return {"approved": True}

    calls.speech.available = lambda: True
    calls.speech.prepare, calls.speech.synthesize = prepare, synthesize
    calls.reasoner.plan, calls.reasoner.answer_stream, calls.reasoner.check = plan, stream, check
    return service, calls, sid


async def drain(service):
    async with asyncio.timeout(3):
        while service.tasks:
            await asyncio.gather(*list(service.tasks), return_exceptions=True)


def test_auto_text_and_voice_reply_before_model_finishes(tmp_path):
    async def run():
        service, calls, sid = fixture(tmp_path)
        queue = asyncio.Queue(maxsize=64)
        service.replies.subscribers[sid].add(queue)
        audio_started = asyncio.Event()

        async def stream(*args):
            yield "The first complete sentence. "
            await asyncio.wait_for(audio_started.wait(), 1)
            yield "The model is now finishing."

        async def listen():
            while True:
                event = await queue.get()
                if isinstance(event, bytes):
                    assert event[:4] == b"DVA4"
                    assert len(event) == 32 + struct.unpack_from("<H", event, 28)[0] * 2
                    audio_started.set()
                    return

        calls.reasoner.answer_stream = stream
        listener = asyncio.create_task(listen())
        await service.submit(sid, "Please explain.")
        await drain(service)
        await listener
        state = service.get(sid)
        assert state["questions"][0]["status"] == "answered"
        assert state["answers"][0]["status"] == "completed"
        assert state["answers"][0]["human_reviewed"] is False
        assert len(state["answers"][0]["segments"]) == 2
        assert state["answers"][0]["metrics"]["first_substantive_ms"] < state["answers"][0]["metrics"]["model_done_ms"]
        await service.submit(sid, "A voice message.", mode="voice")
        await drain(service)
        assert len(service.snapshot(sid, "talker")["answers"]) == 2

    asyncio.run(run())


def test_only_actual_human_evidence_waits_and_other_questions_answer(tmp_path):
    async def run():
        service, calls, sid = fixture(tmp_path)

        async def plan(*args):
            return [
                {"text": "Meaning of life?", "category": "open_domain", "action": "answer"},
                {"text": "Which one?", "category": "ambiguous", "action": "clarify"},
                {"text": "Actual DAU?", "category": "data_lookup", "action": "await_human"},
            ]

        calls.reasoner.plan = plan
        await service.submit(sid, "Three questions.")
        await drain(service)
        state = service.get(sid)
        assert [q["status"] for q in state["questions"]] == ["answered", "awaiting_user", "held"]
        assert len(state["answers"]) == 3
        assert "can't verify" in state["answers"][-1]["text"]
        q = state["questions"][-1]
        await service.replies.resolve(sid, q["id"], {"hint": "昨天的DAU为120，已核实。"})
        await drain(service)
        assert service.get(sid)["questions"][-1]["status"] == "answered"
        assert "昨天的DAU" not in str(service.snapshot(sid, "talker"))

    asyncio.run(run())


def test_hint_during_native_synthesis_discards_old_audio_and_survives_projection_failure(tmp_path):
    async def run():
        service, calls, sid = fixture(tmp_path)
        began, release = asyncio.Event(), asyncio.Event()
        spoken = []

        async def synthesize(text):
            if not began.is_set():
                began.set()
                await release.wait()
            spoken.append(text)
            return bytes(1920), 24000

        calls.speech.synthesize = synthesize
        await service.submit(sid, "Please explain.")
        await asyncio.wait_for(began.wait(), 1)
        await service.guidance.submit(sid, {"text": "只给简短回答。", "kind": "style"})
        release.set()
        await drain(service)
        state = service.get(sid)
        assert any(a["status"] == "interrupted" for a in state["answers"])
        assert any(a["status"] == "completed" for a in state["answers"])
        assert "只给简短回答" in service.guidance.snapshot(sid)[1]
        assert "只给简短回答" not in str(service.snapshot(sid, "talker"))
        records = service.guidance.records(state)
        assert records and service.store.guidance([service.guidance.owner(state, "session")])

    asyncio.run(run())
