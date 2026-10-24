"""Durable queue, adaptive specialist routing, retry, cancellation and workspace isolation.

Tests use offline model stubs; they do not assert external provider quality.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app import store
from app.main import app

from app.agent_jobs import run_one_stage, utc
from app.agent_worker import run_sweep
from app.live_models import AgentJob, LiveIncident, session_factory


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "jobs.db").as_posix())
    monkeypatch.setenv("SESSION_SECRET", "test-secret-value-with-more-than-32-characters")
    monkeypatch.setenv("REGISTRATION_ENABLED", "true")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AUTH_REQUIRE_DURABLE_LIMITS", raising=False)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "demo.db")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.upgrade(config, "head")
    with TestClient(app, headers={"Origin": "http://localhost:3000"}) as browser:
        yield browser


def _incident(client, register, upload, products, inventory, name):
    workspace = register(client, name + "@example.test", "Durable " + name)
    upload(client, workspace, "products", products)
    upload(client, workspace, "inventory", inventory)
    incident = next(row for row in client.get(
        f"/live/workspaces/{workspace}/incidents").json()
        if row["kind"] == "inventory_mismatch")
    return workspace, incident


def test_durable_replan_critic_and_idempotency(client, monkeypatch):
    """Test passed via the test_live fixture, not an external provider."""
    from app.live_models import LiveIncident
    workspace = client.post("/live/register", json={
        "email": "jobs-owner@example.test",
        "password": "a-long-unique-test-password",
        "workspace_name": "Jobs",
    }).json()["workspaces"][0]["id"]
    p = "external_id,sku,name,price\np1,BAG-1,Canvas,1000.00\n"
    i = ("external_id,sku,location,sellable,reserved,quarantined,observed_at\n"
         "wms,BAG-1,Main,5,0,0,2026-10-15T09:00:00+05:30\n"
         "erp,BAG-1,Main,2,0,0,2026-10-15T09:00:00+05:30\n")
    for kind, data in (("products", p), ("inventory", i)):
        result = client.post(f"/live/workspaces/{workspace}/imports",
                             data={"kind": kind},
                             files={"file": ("fixture.csv", data.encode(), "text/csv")})
        assert result.status_code == 200, result.text
    incident = next(row for row in client.get(f"/live/workspaces/{workspace}/incidents").json()
                    if row["kind"] == "inventory_mismatch")
    ids = [item["id"] for item in incident["evidence"]]
    monkeypatch.setenv("LLM_BASE_URL", "https://offline-model.example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "mock-tool-capable-model")
    monkeypatch.setenv("LLM_API_KEY", "local-test-key")
    monkeypatch.setenv("AGENT_AUTORUN_ENABLED", "false")
    monkeypatch.delenv("REDIS_URL", raising=False)
    rounds = []
    def agent_mock(messages):
        prompt = messages[0]["content"]
        if "independently elicited" in prompt:
            return {"choices": [{"message": {"content": json.dumps({
                "verdict": "supported", "rationale": "Source observations disagree and require a physical recount.",
                "evidence_ids": ids, "open_questions": ["Which location count is correct?"],
            })}}]}
        rounds.append("replan" if json.loads(messages[1]["content"])["replan"] else "plan")
        steps = ([{"tool": "get_order", "arguments": {"id": "absent"}}]
                 if rounds[-1] == "plan" else
                 [{"tool": "trace_inventory", "arguments": {"sku": "BAG-1"}}])
        return {"choices": [{"message": {"content": json.dumps({
            "hypotheses": ["Competing warehouse and ERP quantities"],
            "steps": steps, "open_questions": ["Recount needed"],
        })}}]}
    monkeypatch.setattr("app.live_agentic._completion", agent_mock)
    monkeypatch.setattr("app.live_ai._completion", lambda _: {
        "choices": [{"message": {"content": json.dumps({
            "summary": "The observed BAG-1 inventory records disagree by 3 units.",
            "evidence_ids": ids, "proposed_option_ids": ["hold_disputed_units"],
        })}}]})
    before = client.get(f"/live/workspaces/{workspace}/dashboard").json()["inventory"]
    body = {"incident_id": incident["id"], "idempotency_key": "one-customer-request"}
    response = client.post(f"/live/workspaces/{workspace}/agent/jobs", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    duplicate = client.post(f"/live/workspaces/{workspace}/agent/jobs", json=body)
    assert duplicate.status_code == 202 and duplicate.json()["id"] == job_id
    assert run_sweep(3) == 1
    finished = client.get(f"/live/workspaces/{workspace}/agent/jobs/{job_id}").json()
    assert finished["status"] == "succeeded", finished
    assert finished["progress"]["completed_plans"] == 2
    assert finished["model_call_slots_reserved"] == 7
    assert finished["estimated_cost_microusd"] <= finished["max_cost_microusd"]
    assert rounds == ["plan", "replan"]
    detail = client.get(f"/live/workspaces/{workspace}/incidents/{incident['id']}").json()
    findings = detail["investigations"]
    assert len(findings) == 1
    assert findings[0]["mode"] == "ai_enabled"
    assert any(t["tool"] == "agent_critic" and t["status"] == "passed"
               for t in findings[0]["tool_trace"])
    assert any(t["tool"] == "agent_replanner" for t in findings[0]["tool_trace"])
    assert client.get(f"/live/workspaces/{workspace}/dashboard").json()["inventory"] == before
    assert run_sweep(3) == 0

    other = client.post(f"/live/workspaces/{workspace}/agent/jobs",
                        json={"incident_id": incident["id"], "idempotency_key": "cancellable"})
    assert other.status_code == 202
    other_id = other.json()["id"]
    cancelled = client.post(f"/live/workspaces/{workspace}/agent/jobs/{other_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    resumed = client.post(f"/live/workspaces/{workspace}/agent/jobs/{other_id}/resume")
    assert resumed.json()["status"] == "queued"


def test_durable_budget_failure_and_deadline(client, monkeypatch):
    workspace = client.post("/live/register", json={
        "email": "jobs-budget@example.test",
        "password": "a-long-unique-test-password", "workspace_name": "Budgets",
    }).json()["workspaces"][0]["id"]
    with session_factory()() as db:
        incident = LiveIncident(workspace_id=workspace, kind="inventory_mismatch",
                                classification="confirmed_inconsistency",
                                status="open", fingerprint="budget-case",
                                summary="Sample incident for budget checks",
                                evidence=[])
        db.add(incident)
        db.commit()
        incident_id = incident.id
    monkeypatch.setenv("LLM_BASE_URL", "https://offline-model.example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "mock-model")
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    monkeypatch.setenv("AGENT_AUTORUN_ENABLED", "false")
    r = client.post(f"/live/workspaces/{workspace}/agent/jobs",
                    json={"incident_id": incident_id, "idempotency_key": "cheap-run",
                          "max_cost_microusd": 10000})
    assert r.status_code == 202, r.text
    assert run_one_stage() is not None
    status = client.get(f"/live/workspaces/{workspace}/agent/jobs/{r.json()['id']}").json()
    assert status["status"] == "failed" and status["model_call_slots_reserved"] == 0

    fresh = client.post(f"/live/workspaces/{workspace}/agent/jobs",
                        json={"incident_id": incident_id, "idempotency_key": "expired-run"})
    with session_factory()() as db:
        job = db.get(AgentJob, fresh.json()["id"])
        job.deadline_at = utc() - timedelta(minutes=1)
        db.commit()
    run_one_stage()
    expired = client.get(f"/live/workspaces/{workspace}/agent/jobs/{fresh.json()['id']}").json()
    assert expired["status"] == "failed"


def test_related_incidents_do_not_cross_workspaces(client):
    response = client.post("/live/register", json={
        "email": "related-owner@example.test",
        "password": "a-long-unique-test-password", "workspace_name": "Related",
    })
    workspace = response.json()["workspaces"][0]["id"]
    reference = {"kind": "inventory", "id": "reference-one", "external_id": "shared-ID"}
    with session_factory()() as db:
        a = LiveIncident(workspace_id=workspace, fingerprint="related-A", kind="inventory_mismatch",
                         classification="suspected", status="open",
                         summary="First incident", evidence=[reference])
        b = LiveIncident(workspace_id=workspace, fingerprint="related-B", kind="fulfillment_failure",
                         classification="suspected", status="open",
                         summary="Second incident", evidence=[reference])
        db.add_all([a, b])
        db.commit()
        root_id, linked_id = a.id, b.id
    path = f"/live/workspaces/{workspace}/incidents/{root_id}/related"
    found = client.get(path)
    assert found.status_code == 200
    assert [item["incident_id"] for item in found.json()["related"]] == [linked_id]
    assert "not proof" in found.json()["disclaimer"]


def test_expired_lease_is_fenced_and_cancelled_job_can_resume(client):
    """Stale owners must not commit, even if the model request completed."""
    from app.agent_jobs import _claim, _finish, _fail, _reserve, enqueue, resume, request_cancel
    from app.live_models import Workspace

    account = client.post("/live/register", json={
        "email": "lease-owner@example.test",
        "password": "a-long-unique-test-password",
        "workspace_name": "Leases",
    })
    assert account.status_code == 200, account.text
    workspace_id = account.json()["workspaces"][0]["id"]
    with session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        incident = LiveIncident(workspace_id=workspace_id, kind="inventory_mismatch",
                                classification="suspected", status="open",
                                fingerprint="test-fence", summary="Lease fencing test",
                                evidence=[])
        db.add(incident)
        db.flush()
        job = enqueue(db, incident, workspace.owner_id, "stale-first")
        db.commit()
        job_id = job.id
    original = _claim("one")
    assert original and original[0] == job_id
    old_token = original[1]
    with session_factory()() as db:
        job = db.get(AgentJob, job_id)
        job.lease_until = utc() - timedelta(seconds=10)
        db.commit()
    reclaimed = _claim("two")
    assert reclaimed and reclaimed[0] == job_id and reclaimed[1] != old_token
    assert _finish(job_id, old_token, "plan", {"phase": "evidence"}) is False
    with session_factory()() as db:
        job = db.get(AgentJob, job_id)
        request_cancel(db, job)
        db.commit()
    assert _finish(job_id, reclaimed[1], "plan", {"phase": "evidence"}) is True
    with session_factory()() as db:
        job = db.get(AgentJob, job_id)
        assert job.status == "cancelled" and job.investigation_id is None
        resume(db, job)
        db.commit()
    third = _claim("three")
    assert third and third[0] == job_id
    assert _reserve(job_id, third[1], "plan") is True
    _fail(job_id, third[1], RuntimeError("model offline"))
    with session_factory()() as db:
        job = db.get(AgentJob, job_id)
        assert job.status == "queued" and job.attempt == 1
        assert job.next_attempt_at is not None
        job.next_attempt_at = utc() - timedelta(seconds=1)
        db.commit()
    retry = _claim("four")
    assert retry and retry[0] == job_id
    assert _reserve(job_id, retry[1], "plan") is True
    assert _finish(job_id, retry[1], "plan", {"phase": "evidence"}) is True
    with session_factory()() as db:
        job = db.get(AgentJob, job_id)
        assert job.status == "queued" and job.phase == "evidence" and job.attempt == 0
        assert job.reserved_model_calls == 2  # No refund of already reserved calls.
