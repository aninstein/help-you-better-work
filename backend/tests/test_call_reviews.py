"""Direct call replies keep review fencing, private hints and final checks intact."""

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.adapters.store import Store
from app.application.calls import LiveCall
from app.domain.models import SessionRequest
from app.main import create_app


def setup_call(tmp_path, category="open_domain", status="held"):
    app = create_app(Store(tmp_path / "duet.db"))
    service, calls = app.state.service, app.state.calls
    state = service.create(SessionRequest())
    state["interaction_mode"] = "realtime_call"
    state["questions"].append(
        {
            "id": "q1",
            "display_no": "Q-201",
            "text": "What is the meaning of life?",
            "status": status,
            "version": 1,
            "duplicate_count": 1,
            "human_reviewed": False,
            "created_at": state["created_at"],
            "call_id": "call1",
            "category": category,
        }
    )
    state["call"] = {
        "id": "call1",
        "status": "listening",
        "epoch": 1,
        "paused": False,
        "constraints": [],
        "constraints_revision": 0,
        "checks": [],
        "alerts": [{"id": "alert1", "question_id": "q1", "status": "open"}],
    }
    service.store.save(state)
    live = LiveCall(state["id"], "call1", socket=object())
    calls.live["call1"] = live
    return app, service, calls, state["id"], live


def request(decision="auto_answer", **extra):
    return {"question_id": "q1", "version": 1, "decision": decision, **extra}


@pytest.mark.parametrize("category", ["open_domain", "ambiguous", "data_lookup"])
@pytest.mark.parametrize("status", ["held", "expired"])
def test_direct_reply_without_hint_queues_once_and_ignores_unsent_hint(tmp_path, category, status):
    async def run():
        _, service, calls, sid, live = setup_call(tmp_path, category, status)
        started, release = asyncio.Event(), asyncio.Event()
        replies = []

        async def previous():
            started.set()
            await release.wait()

        async def respond(current, qid, **options):
            replies.append(qid)

        live.work = asyncio.create_task(previous())
        await started.wait()
        calls.respond = respond
        assert await calls.review(sid, "call1", request(hint="UNSUBMITTED_PRIVATE_HINT")) == {
            "ok": True
        }
        fresh = service.get(sid)
        assert fresh["questions"][0]["status"] == "generating"
        assert fresh["call"]["alerts"][0]["status"] == "resolved"
        assert not fresh["call"]["constraints"]
        assert "UNSUBMITTED_PRIVATE_HINT" not in str(fresh["messages"])
        with pytest.raises(HTTPException) as error:
            await calls.review(sid, "call1", request())
        assert error.value.status_code == 409
        assert not replies
        release.set()
        await live.work
        assert replies == ["q1"]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("decision", "status", "version", "expected"),
    [
        ("invalid", "held", 1, 400),
        ("auto_answer", "blocked", 1, 409),
        ("auto_answer", "withdrawn", 1, 409),
        ("auto_answer", "held", 0, 409),
        ("approve", "held", 1, 400),
    ],
)
def test_invalid_stale_blocked_and_data_without_verified_hint_do_not_generate(
    tmp_path, decision, status, version, expected
):
    async def run():
        _, service, calls, sid, live = setup_call(tmp_path, "data_lookup", status)
        with pytest.raises(HTTPException) as error:
            await calls.review(sid, "call1", request(decision, version=version))
        assert error.value.status_code == expected
        assert service.get(sid)["questions"][0]["status"] == status
        assert live.work is None

    asyncio.run(run())


@pytest.mark.parametrize("paused", [False, True])
def test_disconnected_or_paused_call_preserves_question_for_retry(tmp_path, paused):
    async def run():
        _, service, calls, sid, live = setup_call(tmp_path)
        if paused:
            state = service.get(sid)
            state["call"]["paused"] = True
            service.store.save(state)
        else:
            live.socket = None
        with pytest.raises(HTTPException) as error:
            await calls.review(sid, "call1", request())
        assert error.value.status_code == 409
        fresh = service.get(sid)
        assert fresh["questions"][0]["status"] == "held"
        assert fresh["call"]["alerts"][0]["status"] == "open"
        assert live.work is None

    asyncio.run(run())


def test_direct_reply_still_requires_final_wiki_check(tmp_path):
    async def run():
        _, service, calls, sid, live = setup_call(tmp_path)
        checked = []

        async def answer(*args):
            return "A candidate answer."

        async def check(question, candidate, wiki, constraints):
            checked.append(candidate)
            return {"approved": False, "reason": "回答与已知约束冲突"}

        calls.reasoner.answer = answer
        calls.reasoner.check = check
        await calls.review(sid, "call1", request())
        await live.work
        fresh = service.get(sid)
        assert checked == ["About your earlier question: A candidate answer."]
        assert fresh["questions"][0]["status"] == "held"
        assert not fresh["answers"]
        assert fresh["call"]["checks"][-1]["approved"] is False
        assert fresh["call"]["alerts"][-1]["status"] == "open"

    asyncio.run(run())


def test_direct_reply_generates_checks_and_streams_audio_without_a_draft(tmp_path):
    async def run():
        _, service, calls, sid, live = setup_call(tmp_path, "data_lookup")
        events, audio = [], []

        class Socket:
            async def send_json(self, event):
                events.append(event)

            async def send_bytes(self, pcm):
                audio.append(pcm)

        live.socket = Socket()

        async def answer(question, wiki, constraints, history):
            assert not constraints.strip()
            return "I cannot verify that figure with the available information."

        async def check(question, candidate, wiki, constraints):
            return {"approved": True, "reason": "未编造业务数据"}

        async def synthesize(text):
            return bytes(2), 24000

        calls.reasoner.answer = answer
        calls.reasoner.check = check
        calls.speech.synthesize = synthesize
        await calls.review(sid, "call1", request())
        await live.work
        fresh = service.get(sid)
        assert len(fresh["answers"]) == 1
        assert fresh["answers"][0]["status"] == "completed"
        assert fresh["answers"][0]["delivery"] == "speaking"
        assert [event["type"] for event in events] == ["answer", "audio", "audio_end"]
        assert audio

    asyncio.run(run())


def test_only_supervisor_can_choose_direct_reply(tmp_path):
    app, _, _, sid, _ = setup_call(tmp_path)
    with TestClient(app) as client:
        token = client.post("/api/v1/auth/demo", json={"role": "talker"}).json()["token"]
        response = client.post(
            f"/api/v1/sessions/{sid}/calls/call1/reviews",
            headers={"Authorization": f"Bearer {token}"},
            json=request(),
        )
        assert response.status_code == 403
