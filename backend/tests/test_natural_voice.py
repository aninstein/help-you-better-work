"""Focused regressions for adaptive handoff, intent and missing audio."""

import asyncio
import json
import struct
import threading
from types import SimpleNamespace

import pytest

from app.adapters.store import Store
from app.agent.transcript_refiner import TranscriptRefiner, clear_independent_requests, disagreement
from app.application.calls import LiveCall
from app.application.voice_control import MediaSequence, TurnController, interruption_decision
from app.domain.models import SessionRequest
from app.main import create_app


def test_handoff_leaves_semantic_slots_to_primary_and_respects_explicit_thinking():
    control = TurnController()
    control.observe(("turn", 1))
    control.prediction = {"status": "ready", "p_complete": 0.99}
    assert control.decide("What is an API?", 600)[0] == "commit"
    assert control.decide("I'd like to know what the figures are.", 600)[0] == "commit"
    control.prediction = {"status": "ready", "p_complete": 0.1}
    assert control.decide("I want to know whether", 2500)[0] in {"wait", "candidate"}
    assert control.decide("My account number is", 5000)[0] == "wait"
    assert control.decide("My account number is", 6200)[0] == "commit"
    assert control.decide("Wait, let me think", 30000) == ("wait", "explicit_wait")
    control.prediction = {"status": "timeout"}
    assert control.decide("What is an API?", 2999)[0] == "wait"
    assert control.decide("What is an API?", 3000) == ("commit", "endpoint_fallback")
    control.observe(("turn", 2))
    assert control.prediction == {} and control.attempts == 0


def test_acknowledgements_are_not_duration_based_interruptions():
    assert interruption_decision("yeah", True, 1300) == "backchannel"
    assert interruption_decision("yeah", True, 100, True) == "interrupt"
    assert interruption_decision("Stop!", False, 100) == "interrupt"
    assert interruption_decision("yeah but what about cost", True, 350) == "interrupt"
    assert interruption_decision("", True, 3000) == "candidate"


def test_media_loss_duplicate_and_invalid_frames_are_visible():
    media = MediaSequence()

    def packet(seq, start):
        return b"DVP3" + struct.pack("<IQHH", seq, start, 512, 0) + bytes(1024)

    assert media.unpack(packet(7, 3584)) == (bytes(1024), False)
    assert media.unpack(packet(9, 4608))[1]  # One dropped frame, not silence.
    assert media.unpack(packet(9, 4608))[1]  # Duplicate.
    with pytest.raises(ValueError):
        media.unpack(packet(10, 5120)[:-2])


def test_quality_evidence_still_cleans_input_and_protects_critical_conflicts():
    async def run():
        class NoModel:
            calls = 0

            def bind(self, **kwargs):
                return self

            async def ainvoke(self, messages):
                self.calls += 1
                data = json.loads(messages[-1].content)
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "text": data["final_asr"] or data["raw_transcript"],
                            "status": "corrected",
                            "unclear_spans": [],
                        }
                    )
                )

        settings = SimpleNamespace(model="test", model_label="test", base_url="http://local")
        model = NoModel()
        refiner = TranscriptRefiner(SimpleNamespace(clients=[(settings, model)]))
        final = {
            "status": "ready",
            "text": "What is an API?",
            "quality": {
                "evidence": "whisper_segment_and_word_scores",
                "min_word_probability": 0.9,
                "min_avg_logprob": -0.1,
                "max_no_speech_prob": 0.01,
                "max_compression_ratio": 1,
            },
        }
        assert (await refiner.refine("WHAT IS AN API", final, []))["status"] == "corrected"
        assert model.calls == 1
        final["text"] = "Query ID A7B8."
        assert (await refiner.refine("QUERY ID A7B9", final, []))["status"] == "needs_clarification"
        assert disagreement("do not query ID A7B9", "Query ID A7B9")
        assert disagreement("query fifty accounts", "query accounts")
        clear, uncertain = clear_independent_requests(
            "WHAT IS AN API? QUERY ID A7B9.",
            "What is an API? Query ID A7B8.",
        )
        assert clear == "What is an API?" and uncertain == ["Query ID A7B8"]

    asyncio.run(run())


def test_old_endpoint_result_cannot_commit_resumed_speech(tmp_path):
    async def run():
        app = create_app(Store(tmp_path / "duet.db"))
        calls = app.state.calls
        state = app.state.service.create(SessionRequest())
        live = LiveCall(state["id"], "test", protocol=3)
        calls.live["test"] = live
        live.turn.id, live.turn.revision = "turn", 1
        live.turn.state, live.turn.sample = "waiting_pause", 16000
        live.turn.partial = "What is an API?"
        release = threading.Event()

        def predict(audio):
            release.wait(1)
            return {"status": "ready", "p_complete": 1}

        calls.endpoint.predict = predict
        signals = {"ready": False}
        await calls.adaptive_endpoint(live, signals)
        live.turn.revision += 1
        live.turn.state = "collecting"
        release.set()
        await live.endpoint_work
        assert not live.controller.prediction and not signals["ready"]
        assert not app.state.service.get(state["id"])["messages"]

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["planning", "checking"])
def test_recognized_speech_returns_thinking_to_listening(tmp_path, stage):
    import numpy as np
    from test_call_reviews import setup_call

    async def run():
        _, service, calls, sid, live = setup_call(tmp_path, status="generating")
        events = []

        class Socket:
            async def send_json(self, event):
                events.append(event)

            async def send_bytes(self, frame):
                raise AssertionError("Interrupted thinking must not publish old audio.")

        live.socket = None
        live.protocol = 3
        live.stream = object()
        calls.endpoint.session = object()
        live.vad = SimpleNamespace(accept=lambda samples: 0.99, reset=lambda: None)
        transcript = [""]
        calls.speech.recognizer = SimpleNamespace(reset=lambda stream: None)
        calls.speech.accept = lambda stream, pcm: (transcript[0], False)
        await calls.attach(sid, live.call_id, Socket())
        state = service.get(sid)
        assert state["call"]["status"] == state["call"]["activity_state"] == "listening"
        state["call"].update(status="thinking", muted=False, active_message_id="old-input")
        state["questions"][0]["source_message_ids"] = ["old-input"]
        calls.commit(state)
        blocked, release = asyncio.Event(), asyncio.Event()

        async def old_work(*args):
            blocked.set()
            await release.wait()
            return {"approved": True}

        supplement = asyncio.create_task(asyncio.Event().wait())
        live.responses["human-supplement"] = supplement
        if stage == "planning":
            live.intake_work = asyncio.create_task(old_work())
            old_task = live.intake_work
        else:
            calls.reasoner.check = old_work
            old_task = calls.queue_response(live, "q1", candidate="The old answer.")
        await blocked.wait()
        frame = np.full(512, 30000, dtype="<i2").tobytes()

        async def receive(index):
            packet = b"DVP3" + struct.pack("<IQHH", index, index * 512, 512, 0) + frame
            await calls.receive_audio(live, packet)

        for index in range(5):
            await receive(index)
        assert not old_task.cancelling()  # Human sound alone is not a recognized word.
        transcript[0] = "LONDON"
        await receive(5)
        await asyncio.gather(old_task, return_exceptions=True)
        state = service.get(sid)
        assert old_task.cancelled()
        assert state["call"]["status"] == state["call"]["activity_state"] == "listening"
        assert live.turn.state == "collecting" and live.turn.text == "LONDON"
        assert not state["answers"]
        assert any(e["type"] == "interruption.confirmed" for e in events)
        assert not supplement.cancelling()  # Unstarted human supplements survive.
        supplement.cancel()
        await asyncio.gather(supplement, return_exceptions=True)

    asyncio.run(run())
