"""Small regressions for conversational boundaries, late edits and direct refinement."""

import asyncio
import json
from types import SimpleNamespace

import numpy as np

from app.adapters.store import Store
from app.agent.transcript_refiner import TranscriptRefiner, readable
from app.application.calls import LiveCall
from app.application.turns import TurnAssembler
from app.domain.models import SessionRequest
from app.main import create_app

FRAME = bytes(1024)


def frames(turn, probability, count):
    return [turn.accept(FRAME, probability) for _ in range(count)]


def test_short_pause_joins_segments_and_three_seconds_freezes_once():
    turn = TurnAssembler()
    frames(turn, 0.99, 20)
    first_id = turn.id
    turn.transcript("COULD YOU COMPARE", True)
    assert not any(r["ready"] for r in frames(turn, 0, 62))  # 1.984 seconds
    frames(turn, 0.99, 20)
    assert turn.id == first_id
    turn.transcript("THESE TWO OPTIONS", True)
    results = frames(turn, 0, 94)
    assert sum(r["ready"] for r in results) == 1
    assert turn.text == "COULD YOU COMPARE THESE TWO OPTIONS"
    assert turn.state == "finalizing"
    old_key = turn.key
    frames(turn, 0.99, 4)
    assert turn.key != old_key and turn.id == first_id
    assert turn.state == "collecting"


def test_noise_does_not_start_turn_and_limit_does_not_submit():
    turn = TurnAssembler(max_seconds=1)
    assert not any(r["started"] for r in frames(turn, 0.99, 2))
    frames(turn, 0, 100)
    assert not turn.id
    frames(turn, 0.99, 40)
    assert turn.state == "input_limit"
    assert not any(r["ready"] for r in frames(turn, 0, 100))
    assert not turn.id


def test_refinement_resumption_drops_late_result(tmp_path):
    async def run():
        app = create_app(Store(tmp_path / "duet.db"))
        service, calls = app.state.service, app.state.calls
        state = service.create(SessionRequest())
        state["call"] = {
            "id": "test",
            "status": "listening",
            "epoch": 1,
            "paused": False,
            "muted": False,
            "constraints": [],
            "constraints_revision": 0,
            "alerts": [],
        }
        state["interaction_mode"] = "realtime_call"
        service.store.save(state)
        started, release = asyncio.Event(), asyncio.Event()
        live = LiveCall(state["id"], "test", stream=object())
        live.vad = SimpleNamespace(accept=lambda s: float(s[0]), reset=lambda: None)
        calls.live["test"] = live
        calls.speech.recognizer = SimpleNamespace(reset=lambda stream: None)
        calls.speech.accept = lambda stream, pcm: ("CAN YOU EXPLAIN DAU", False)

        async def final_asr(pcm):
            return {"text": "Can you explain DAU?", "status": "ready"}

        attempts = 0

        async def refine(raw, final, recent):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()  # Emulate an upstream worker returning after cancellation.
                text = "STALE_CORRECTION"
            else:
                text = "Can you explain DAU and API limits?"
            return {
                "text": text,
                "status": "corrected",
                "model": "fake",
                "route": "direct",
                "reason": "",
                "unclear_spans": [],
                "refiner_ms": 1,
            }

        calls.speech.transcribe_turn = final_asr
        calls.refiner.refine = refine
        main_inputs = []

        async def process(live, message_id, epoch, *timing):
            main_inputs.append(message_id)

        calls.process = process
        voiced = np.full(512, 30000, dtype="<i2").tobytes()
        for _ in range(15):
            await calls.receive_audio(live, voiced)
        for _ in range(94):
            await calls.receive_audio(live, FRAME)
        await asyncio.wait_for(started.wait(), 1)
        # Finished speech must not interrupt its own thinking phase when further
        # silent PCM arrives. Historical max speech duration is not current speech.
        for _ in range(4):
            await calls.receive_audio(live, FRAME)
        assert service.get(state["id"])["call"]["epoch"] == 1
        assert not service.get(state["id"])["messages"]
        await calls.intervene(state["id"], "test", {"action": "add_for_next", "text": "只说两句"})
        for _ in range(15):
            await calls.receive_audio(live, voiced)
        for _ in range(94):
            await calls.receive_audio(live, FRAME)
        release.set()
        await asyncio.gather(*list(service.tasks))
        await asyncio.sleep(0)
        messages = [m for m in service.get(state["id"])["messages"] if m["type"] == "talker"]
        assert len(messages) == len(main_inputs) == 1
        assert messages[0]["text"] == "Can you explain DAU and API limits?"
        assert messages[0]["transcription"]["raw_transcript"]
        assert "STALE_CORRECTION" not in str(messages)
        assert "只说两句" not in str(messages[0]["transcription"])
        await calls.cancel_input(live, "muted")
        assert not live.turn.id

    asyncio.run(run())


def test_direct_refiner_preserves_slots_and_does_not_fail_over():
    class Model:
        calls = 0
        reply = ""
        slow = False

        def bind(self, **options):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.slow:
                await asyncio.sleep(1)
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "text": self.reply,
                        "status": "corrected",
                        "unclear_spans": [],
                    }
                )
            )

    async def run():
        primary, fallback = Model(), Model()
        settings = SimpleNamespace(model="fast", model_label="fast", base_url="http://local")
        refiner = TranscriptRefiner(
            SimpleNamespace(clients=[(settings, primary), (settings, fallback)])
        )
        primary.reply = "Can you explain DAU and API limits?"
        result = await refiner.refine("CAN YOU EXPLAIN DAU AND API LIMITS", {}, [])
        assert result["status"] == "corrected" and "DAU" in result["text"]
        primary.reply = "Query user ID A7B8."
        result = await refiner.refine("DO NOT QUERY USER ID A7B9", {}, [])
        assert result["status"] == "needs_clarification" and "not" in result["text"]
        primary.reply = "There are fifty accounts."
        result = await refiner.refine(
            "THERE ARE FIFTEEN ACCOUNTS", {"text": "There are fifty accounts."}, []
        )
        assert result["status"] == "needs_clarification"
        primary.slow = True
        refiner.timeout = 0.01
        result = await refiner.refine("CAN YOU EXPLAIN DAU", {}, [])
        assert result["status"] == "degraded"
        assert fallback.calls == 0
        assert readable("DO NOT QUERY ID A7B9") == "Do not query ID A7B9"

    asyncio.run(run())
