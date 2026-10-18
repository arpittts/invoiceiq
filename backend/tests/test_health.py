from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)


def test_health_ok_when_database_reachable():
    # Talks to the real local database (docker compose up -d db).
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unreachable_database(monkeypatch):
    monkeypatch.setattr(main, "check_database", lambda: False)
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}
