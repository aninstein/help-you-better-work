"""Small live API admission check; run while the app is listening on 8818."""

import os

import httpx

base = os.getenv("DUET_URL", "http://127.0.0.1:8818")
with httpx.Client(base_url=base, timeout=15) as client:
    assert client.get("/healthz").json()["status"] == "ok"
    identities = {}
    for role in ["talker", "hinter"]:
        login = client.post("/api/v1/auth/demo", json={"role": role}).json()
        assert login["user_id"]
        identities[role] = {"Authorization": "Bearer " + login["token"]}
    talker, hinter = identities["talker"], identities["hinter"]
    session = client.post("/api/v1/sessions", headers=talker, json={}).json()
    path = "notes/admission-check.md"
    saved = client.put(
        "/api/v1/wiki/document",
        params={"path": path},
        headers=talker,
        json={"content": "# Admission check\n\nWiki content stays across sessions.\n"},
    )
    assert saved.status_code == 200
    assert (
        client.get("/api/v1/wiki/document", params={"path": path}, headers=hinter).status_code
        == 404
    )
    reset = client.post(f"/api/v1/sessions/{session['id']}/reset", headers=talker).json()
    assert reset["id"] != session["id"] and reset["messages"] == []
    assert reset["user_id"] == session["user_id"]
    assert (
        client.get("/api/v1/wiki/document", params={"path": path}, headers=talker).status_code
        == 200
    )
    client.delete("/api/v1/wiki/document", params={"path": path}, headers=talker)
    print(
        "PASS: health, stable identities, isolated editable Wiki, new Session with preserved Wiki"
    )
