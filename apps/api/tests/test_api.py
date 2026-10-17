import time

from fastapi.testclient import TestClient

from app import store
from app.main import app


def test_flagship_api_journey(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "api.db")
    with TestClient(app) as client:
        created = client.post("/runs", json={"seed": 2026}).json()
        run_id = created["run"]["run_id"]
        assert created["metrics"]["order_count"] == 18
        assert created["run"]["status"] == "running"
        paused = client.post(f"/runs/{run_id}/control", json={"action": "pause"}).json()
        sequence = paused["sequence"]
        time.sleep(.6)
        assert client.get(f"/runs/{run_id}").json()["sequence"] == sequence
        stepped = client.post(f"/runs/{run_id}/control", json={"action": "step"}).json()
        assert stepped["sequence"] > sequence
        with client.websocket_connect(f"/runs/{run_id}/stream?after={sequence}") as ws:
            first = ws.receive_json()
            assert first["sequence_number"] == sequence + 1
        incident = client.post(f"/runs/{run_id}/incidents", json={"type": "damaged_return"}).json()
        assert incident["status"] == "awaiting_approval"
        assert len(incident["evidence_events"]) == 2
        traces = client.get(f"/runs/{run_id}/traces").json()
        assert traces[0]["tool_calls"][0]["status"] == "ok"
        comparison = client.post(f"/incidents/{incident['incident_id']}/compare").json()
        assert comparison["baseline_metrics"]["open_incidents"] == 1
        assert comparison["recovery_metrics"]["open_incidents"] == 0
        assert comparison["third_run_id"] not in {comparison["baseline_run_id"], comparison["recovery_run_id"]}
        assert comparison["third_metrics"]["open_incidents"] == 0
        receipt = client.get(f"/incidents/{incident['incident_id']}/receipt")
        assert receipt.status_code == 200 and "SYNTHETIC DEMO" in receipt.text
        assert "Three isolated futures" in receipt.text
        sweep = client.post(f"/incidents/{incident['incident_id']}/sensitivity",
                            json={"review_delay_hours": [0, 24, 48]})
        assert sweep.status_code == 200, sweep.text
        recovery_hours = [item["branch_c"]["recovery_hours"] for item in sweep.json()["rows"]]
        assert recovery_hours[0] < recovery_hours[1] < recovery_hours[2]
        current = client.get(f"/runs/{run_id}").json()
        response = client.post(f"/incidents/{incident['incident_id']}/decision", json={
            "approve": True, "expected_sequence": current["sequence"]})
        assert response.status_code == 200, response.text
        resolved = response.json()
        assert resolved["status"] == "resolved"
        assert resolved["verification"][0]["passed"] == 1
        assert "Sandbox verification: 1" in client.get(f"/incidents/{incident['incident_id']}/receipt").text
        replay = client.get(f"/runs/{run_id}?until={sequence}").json()
        assert replay["sequence"] == sequence
        assert replay["metrics"]["open_incidents"] == 0
