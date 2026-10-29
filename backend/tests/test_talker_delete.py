"""Deleting one workspace removes its memory without resurrecting stale writes."""

import copy

import pytest
from fastapi.testclient import TestClient

from app.adapters.store import Store
from app.adapters.wiki import USER_IDS
from app.main import create_app


def setup(tmp_path):
    app = create_app(Store(tmp_path / "duet.db"))
    client = TestClient(app)
    token = client.post("/api/v1/auth/demo", json={"role": "hinter"}).json()["token"]
    return app.state.service, client, {"Authorization": f"Bearer {token}"}


def test_hinter_deletes_talker_sessions_wiki_and_private_data_without_affecting_others(tmp_path):
    service, client, headers = setup(tmp_path)
    target = service.create_talker("Delete me")
    other = service.create_talker("Keep me")
    sid = target["latest_session_id"]
    wiki = service.wiki_for(sid)
    other_wiki = service.wiki_for(other["latest_session_id"])
    wiki.write("talker", "notes/facts.md", "TARGET_PUBLIC_MEMORY")
    wiki.write("hinter", "notes/private.md", "TARGET_PRIVATE_MEMORY")
    other_wiki.write("talker", "notes/facts.md", "KEEP_OTHER_MEMORY")
    stale = copy.deepcopy(service.get(sid))
    stale["messages"].append({"filename": "target.wav"})
    service.store.save(stale, {"sequence": 1, "type": "test"})
    service.store.save_command(sid + ":review:test", "hash", {"private": "old hint"})
    guidance = {"id": "g1", "talker_id": target["id"], "text": "TARGET_PRIVATE_HINT"}
    service.store.append_guidance("test-scope", guidance)
    (service.audio_dir / "target.wav").write_bytes(b"audio")
    response = client.delete(f"/api/v1/talkers/{target['id']}", headers=headers)
    assert response.status_code == 200
    assert target["id"] not in {t["id"] for t in response.json()["items"]}
    assert service.store.load(sid) is None
    assert not service.store.events(sid, 0)
    assert service.store.command(sid + ":review:test") is None
    assert service.store.setting("active_session:" + target["id"]) is None
    assert not service.store.guidance(["test-scope"])
    assert not (service.audio_dir / "target.wav").exists()
    assert not list(wiki.root.rglob("*.md"))
    assert other_wiki.read("talker", "notes/facts.md")["content"] == "KEEP_OTHER_MEMORY"
    with pytest.raises(ValueError):
        service.store.save(stale)
    with pytest.raises(ValueError):
        wiki.write("talker", "context.md", "STALE_MEMORY")
    with pytest.raises(ValueError):
        service.store.append_guidance("test-scope", guidance)
    assert client.get(f"/api/v1/sessions/{sid}", headers=headers).status_code == 404
    assert client.delete(f"/api/v1/talkers/{target['id']}", headers=headers).status_code == 200


def test_default_delete_preserves_siblings_and_survives_restart(tmp_path):
    service, client, headers = setup(tmp_path)
    other = service.create_talker("Remaining Talker")
    wiki = service.wiki_for(other["latest_session_id"])
    wiki.write("talker", "notes/keep.md", "KEEP")
    response = client.delete(f"/api/v1/talkers/{USER_IDS['talker']}", headers=headers)
    assert response.status_code == 200
    restarted = create_app(Store(tmp_path / "duet.db")).state.service
    assert USER_IDS["talker"] not in {t["id"] for t in restarted.directory()}
    assert restarted.talker()["id"] == other["id"]
    assert not (restarted.wiki.root / USER_IDS["talker"]).exists()
    assert (
        restarted.wiki_for(other["latest_session_id"]).read("talker", "notes/keep.md")["content"]
        == "KEEP"
    )
    restarted.delete_talker(other["id"])
    empty = create_app(Store(tmp_path / "duet.db")).state.service
    assert not empty.directory()
    assert empty.create_talker("New start")["name"] == "New start"


def test_delete_requires_hinter_and_blocks_active_calls(tmp_path):
    service, client, headers = setup(tmp_path)
    target = service.create_talker("Active")
    token = client.post(
        "/api/v1/auth/demo", json={"role": "talker", "talker_id": target["id"]}
    ).json()["token"]
    assert (
        client.delete(
            f"/api/v1/talkers/{target['id']}", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 403
    )
    state = service.get(target["latest_session_id"])
    state["call"] = {"id": "call", "status": "listening"}
    service.store.save(state)
    result = client.delete(f"/api/v1/talkers/{target['id']}", headers=headers)
    assert result.status_code == 409
    assert service.talker(target["id"])
    assert client.delete("/api/v1/talkers/not-found", headers=headers).status_code == 404
