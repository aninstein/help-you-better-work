"""Regression coverage for late hints: bounded history, public redaction and stale Wiki writes."""

import asyncio
import copy

from fastapi.testclient import TestClient

from app.adapters.store import Store
from app.adapters.wiki import WikiStore
from app.domain.models import SessionRequest
from app.main import create_app


def fixture(tmp_path):
    app = create_app(Store(tmp_path / "duet.db"))
    service = app.state.service
    state = service.create(SessionRequest())
    for i in range(6):
        qid, aid = f"q{i}", f"a{i}"
        question = {
            "id": qid,
            "text": f"Question {i}",
            "display_no": f"Q-{i}",
            "status": "answered",
            "version": 1,
            "duplicate_count": 1,
            "human_reviewed": False,
            "created_at": state["created_at"],
        }
        answer = {
            "id": aid,
            "text": f"PRIVATE_ANSWER_{i}",
            "question_ids": [qid],
            "status": "completed",
            "delivery": "delivered",
            "created_at": state["created_at"],
            "human_reviewed": False,
            "tts_audio": {"id": f"audio{i}", "filename": f"audio{i}.wav", "status": "ready"},
        }
        state["questions"].append(question)
        state["answers"].append(answer)
        state["messages"].append({**answer, "type": "ai", "audio": answer["tts_audio"]})
        (service.audio_dir / f"audio{i}.wav").write_bytes(b"audio fixture")
    state["call"] = {
        "id": "call1",
        "status": "listening",
        "epoch": 1,
        "paused": False,
        "constraints": [],
        "constraints_revision": 0,
        "alerts": [],
    }
    service.store.save(state)
    service.wiki.sync_session(state)
    return app, service, state


def test_late_hint_captures_five_and_cannot_resurrect(tmp_path):
    app, service, state = fixture(tmp_path)
    calls = app.state.calls
    sid = state["id"]
    client = TestClient(app)
    token = client.post("/api/v1/auth/demo", json={"role": "talker"}).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    old_audio = client.post(
        f"/api/v1/sessions/{sid}/audio/audio1/playback", headers=headers
    ).json()["playback_url"]
    stale = copy.deepcopy(state)

    async def run():
        started, finish = asyncio.Event(), asyncio.Event()

        async def match(hint, questions):
            assert [q["id"] for q in questions] == [f"q{i}" for i in range(1, 6)]
            started.set()
            await finish.wait()
            return {"is_prohibition": True, "question_ids": ["q1"], "reason": "命中封禁原则"}

        calls.reasoner.retrospect = match
        await service.guidance.submit(
            sid, {"text": "不要讨论这个账户", "effective_from": "now"}, trigger_reply=False
        )
        await started.wait()
        fresh = service.get(sid)
        # A new question must not move the captured oldest question outside the audit window.
        fresh["questions"].append({**fresh["questions"][-1], "id": "q6"})
        # Another answer to the captured question appears while the audit is in flight.
        late = {**fresh["answers"][1], "id": "late", "text": "LATE_PRIVATE_ANSWER"}
        fresh["answers"].append(late)
        fresh["messages"].append({**late, "type": "ai"})
        service.store.save(fresh)
        finish.set()
        await asyncio.gather(*list(service.tasks))

    asyncio.run(run())
    snapshot = client.get(f"/api/v1/sessions/{sid}", headers=headers).json()
    assert "PRIVATE_ANSWER_1" not in str(snapshot)
    assert "LATE_PRIVATE_ANSWER" not in str(snapshot)
    assert "PRIVATE_ANSWER_0" in str(snapshot)
    assert len([m for m in snapshot["messages"] if m["status"] == "withdrawn"]) == 2
    assert client.get(old_audio).status_code == 410
    assert (
        client.post(f"/api/v1/sessions/{sid}/audio/audio1/playback", headers=headers).status_code
        == 410
    )
    assert client.get(f"/api/v1/sessions/{sid}/audio/audio0?token=invalid").status_code == 401
    # A pre-withdrawal background snapshot must not write the answer back into the Wiki.
    service.wiki.sync_session(stale)
    restored = WikiStore(service.wiki.root)
    assert "PRIVATE_ANSWER_1" not in restored.bundle("talker", sid)
    assert "PRIVATE_ANSWER_0" in restored.bundle("talker", sid)


def test_style_hint_does_not_withdraw_and_manual_is_hinter_only(tmp_path):
    app, service, state = fixture(tmp_path)
    calls = app.state.calls
    sid = state["id"]

    async def run():
        async def match(hint, questions):
            return {"is_prohibition": False, "question_ids": [], "reason": "仅调整长度"}

        calls.reasoner.retrospect = match
        await calls.intervene(sid, "call1", {"action": "add_for_next", "text": "只说两句"})
        await asyncio.gather(*list(service.tasks))

    asyncio.run(run())
    assert all(a["status"] == "completed" for a in service.get(sid)["answers"])
    client = TestClient(app)
    talker = client.post("/api/v1/auth/demo", json={"role": "talker"}).json()["token"]
    hinter = client.post("/api/v1/auth/demo", json={"role": "hinter"}).json()["token"]
    url = f"/api/v1/sessions/{sid}/answers/a5/withdraw"
    assert client.post(url, headers={"Authorization": "Bearer " + talker}).status_code == 403
    assert client.post(url, headers={"Authorization": "Bearer " + hinter}).status_code == 200
    # Manual withdrawal covers all history, even after hangup, and targets one bubble only.
    fresh = service.get(sid)
    fresh["call"]["status"] = "ended"
    later_answer = {**fresh["answers"][0], "id": "a0-new", "text": "NEWER_ANSWER"}
    fresh["answers"].append(later_answer)
    fresh["messages"].append({**later_answer, "type": "ai"})
    service.store.save(fresh)
    old_url = f"/api/v1/sessions/{sid}/answers/a0/withdraw"
    assert (
        client.post(old_url, headers={"Authorization": "Bearer " + hinter}).json()
        == {"status": "withdrawn", "withdrawn_count": 1}
    )
    assert (
        client.post(old_url, headers={"Authorization": "Bearer " + hinter}).json()
        == {"status": "withdrawn", "withdrawn_count": 0}
    )
    fresh = service.get(sid)
    assert fresh["answers"][0]["status"] == "withdrawn"
    assert fresh["answers"][5]["status"] == "withdrawn"
    assert fresh["answers"][-1]["status"] == "completed"
    assert fresh["questions"][0]["status"] == "answered"
    for token in [talker, hinter]:
        snapshot = client.get(
            f"/api/v1/sessions/{sid}", headers={"Authorization": "Bearer " + token}
        ).json()
        assert "PRIVATE_ANSWER_0" not in str(snapshot)
        assert "NEWER_ANSWER" in str(snapshot)
