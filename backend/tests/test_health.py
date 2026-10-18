from app.api import health


def test_health_ok_when_database_reachable(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unreachable_database(client, monkeypatch):
    monkeypatch.setattr(health, "check_database", lambda: False)
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}
