from fastapi.testclient import TestClient

from app.main import app


def test_healthz_returns_service_status() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "dual-ai-voice-api"}
