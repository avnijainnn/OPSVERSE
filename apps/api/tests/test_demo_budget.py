"""Anonymous showcase mutations have a bounded single-instance budget."""
from fastapi.testclient import TestClient

from app import store
from app.main import _demo_budget, app


def test_demo_launch_budget_and_run_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "demo.db")
    monkeypatch.setenv("DEMO_MUTATIONS_PER_HOUR", "2")
    monkeypatch.setenv("DEMO_MAX_ROOT_RUNS", "1")
    _demo_budget.clear()
    with TestClient(app) as client:
        assert client.post("/runs", json={"seed": 1}).status_code == 200
        monkeypatch.setenv("PUBLIC_SHOWCASE", "true")
        assert client.get("/runs").json() == []
        monkeypatch.setenv("PUBLIC_SHOWCASE", "false")
        assert len(client.get("/runs").json()) == 1
        capped = client.post("/runs", json={"seed": 2})
        assert capped.status_code == 429
        assert "capacity" in capped.json()["detail"]
        limited = client.post("/runs", json={"seed": 3})
        assert limited.status_code == 429
        assert "mutation limit" in limited.json()["detail"]
    _demo_budget.clear()
