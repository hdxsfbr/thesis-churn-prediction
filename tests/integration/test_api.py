"""The run API: health, a triggered cycle, and the runtime config it reads."""

import time

from fastapi.testclient import TestClient

from churn import runtime_config
from churn.api.app import create_app
from churn.warehouse.migrate import apply_migrations

AS_OF = "2025-06-30"
KEY = {"X-API-Key": "t21-key"}


def _client(wh, settings, monkeypatch) -> TestClient:
    monkeypatch.setenv("CHURN_API_KEY", "t21-key")
    return TestClient(create_app(settings, wh))


def test_health_returns_ok(fresh_wh, settings, monkeypatch):
    client = _client(fresh_wh, settings, monkeypatch)
    response = client.get("/health", headers=KEY)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_api_key_rejected(fresh_wh, settings, monkeypatch):
    client = _client(fresh_wh, settings, monkeypatch)
    response = client.get("/health")
    assert response.status_code == 401
    assert "API key" in response.json()["detail"]


def test_ready_reports_missing_model(fresh_wh, settings, monkeypatch):
    apply_migrations(fresh_wh, settings)
    client = _client(fresh_wh, settings, monkeypatch)
    response = client.get("/ready", headers=KEY)
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert any("promoted model" in reason for reason in body["reasons"])


def test_triggered_run_appears_in_run_log(pipeline, monkeypatch):
    wh, settings = pipeline["wh"], pipeline["settings"]
    client = _client(wh, settings, monkeypatch)
    response = client.post("/runs", headers=KEY, json={"kind": "manual", "as_of": AS_OF, "run_id": "t21-api"})
    assert response.status_code == 200
    assert response.json()["run_id"] == "t21-api"
    status = None
    for _ in range(100):
        found = client.get("/runs/t21-api", headers=KEY)
        if found.status_code == 200:
            status = found.json()["run"]["status"]
            if status in {"SUCCEEDED", "DEGRADED", "FAILED"}:
                break
        time.sleep(0.1)
    assert status == "SUCCEEDED"
    listed = client.get("/runs", headers=KEY)
    assert "t21-api" in {row["run_id"] for row in listed.json()}
    metrics = client.get("/runs/t21-api/metrics", headers=KEY)
    assert metrics.status_code == 200
    assert any(row["metric"] == "coverage" for row in metrics.json())


def test_runtime_config_endpoint_shows_validation_errors(pipeline, monkeypatch):
    wh, settings = pipeline["wh"], pipeline["settings"]
    client = _client(wh, settings, monkeypatch)
    wh.execute("UPDATE CHURN_OPS.AGENT_CONFIG SET value_json = :v WHERE key = 'gate'", {"v": "{"})
    try:
        response = client.get("/config/runtime", headers=KEY)
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert any(error.startswith("gate:") for error in body["errors"])
    finally:
        runtime_config.seed(wh, settings, overwrite=True)
