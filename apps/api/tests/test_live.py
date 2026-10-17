"""Live-data integration journeys use only imported records, never demo fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app import store
from app.live_ai import RelatedArgs, related_records, model_fact, policy_proposals, read_tool, _completion
from app.live_models import InventorySnapshot, LiveIncident, session_factory
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "live.db").as_posix())
    monkeypatch.setenv("SESSION_SECRET", "test-secret-value-with-more-than-32-characters")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "demo.db")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.upgrade(config, "head")
    with TestClient(app, headers={"Origin": "http://localhost:3000"}) as browser:
        yield browser


def register(client, email="owner@example.com", workspace="North Commerce"):
    response = client.post("/live/register", json={"email": email,
        "password": "a-long-unique-test-password", "workspace_name": workspace})
    assert response.status_code == 200, response.text
    return response.json()["workspaces"][0]["id"]


def upload(client, workspace, kind, csv_text, expected=200):
    response = client.post(f"/live/workspaces/{workspace}/imports", data={"kind": kind},
                           files={"file": (kind + ".csv", csv_text.encode(), "text/csv")})
    assert response.status_code == expected, response.text
    return response.json()


def test_model_inputs_omit_source_payload_and_reject_deceptive_local_url(monkeypatch):
    fact = model_fact({"id": "row-1", "external_id": "stock-1",
                       "fields": {"sku": "BAG-1", "sellable": 5, "customer_ref": "private-customer"},
                       "source_row": {"customer_email": "private@example.test"}}, "inventory")
    assert fact["fields"] == {"sku": "BAG-1", "sellable": 5}
    assert "private" not in json.dumps(fact)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost.attacker.example")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("LLM_API_KEY", "test")
    with pytest.raises(ValueError, match="HTTPS"):
        _completion([])


PRODUCTS = "external_id,sku,name,price\np1,BAG-1,Canvas bag,1000.00\n"
INVENTORY = ("external_id,sku,location,sellable,reserved,quarantined,observed_at\n"
             "wms-1,BAG-1,Main,5,0,0,2026-10-15T09:00:00+05:30\n"
             "erp-1,BAG-1,Main,2,0,0,2026-10-15T09:00:00+05:30\n")
ORDERS = ("external_id,customer_ref,status,channel,total,placed_at\n"
          "o-1,C-001,fulfilled,Shopify,1000.00,2026-10-14T10:00:00+05:30\n"
          "o-2,C-002,fulfilled,Shopify,1000.00,2026-10-14T11:00:00+05:30\n")


def test_order_import_persists_and_displays_real_dashboard(client):
    workspace = register(client)
    first = upload(client, workspace, "orders", ORDERS)
    assert first["imported_count"] == 2
    dashboard = client.get(f"/live/workspaces/{workspace}/dashboard").json()
    assert dashboard["mode"] == "live_imported"
    assert dashboard["metrics"]["order_count"] == 2
    assert dashboard["metrics"]["gross_orders_paise"] == 200000
    assert dashboard["connections"]["shopify"] == "not_connected"
    assert all(item["source"] == "csv" for item in dashboard["orders"])
    repeated = upload(client, workspace, "orders", ORDERS)
    assert repeated["imported_count"] == 0 and repeated["duplicate_count"] == 2
    history = client.get(f"/live/workspaces/{workspace}/imports").json()
    assert len(history) == 2
    with TestClient(app, headers={"Origin": "http://localhost:3000"}) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get(f"/live/workspaces/{workspace}/dashboard").json()["metrics"]["order_count"] == 2


def test_incident_graph_resolves_versions_and_enforces_workspace(client):
    workspace = register(client)
    upload(client, workspace, "products", PRODUCTS)
    upload(client, workspace, "inventory", INVENTORY)
    incident = next(item for item in client.get(f"/live/workspaces/{workspace}/incidents").json()
                    if item["kind"] == "inventory_mismatch")
    graph = client.get(f"/live/workspaces/{workspace}/incidents/{incident['id']}/evidence-graph").json()
    assert graph["coverage"]["resolved_sources"] == 2
    assert graph["coverage"]["observation_versions"] == 2
    assert graph["coverage"]["complete"]
    assert {node["classification"] for node in graph["nodes"]} == {"observed", "derived"}
    assert {edge["classification"] for edge in graph["edges"]} == {"observed", "derived", "inferred"}
    assert all(node["record_id"] in {ref["id"] for ref in incident["evidence"]}
               for node in graph["nodes"] if node["id"].startswith("source:"))
    other = register(client, "other@example.com", "Other merchant")
    assert client.get(f"/live/workspaces/{other}/incidents/{incident['id']}/evidence-graph").status_code == 404


def test_read_tools_reject_other_workspace_records(client):
    first = register(client)
    upload(client, first, "products", PRODUCTS)
    upload(client, first, "inventory", INVENTORY)
    incident_id = client.get(f"/live/workspaces/{first}/incidents").json()[0]["id"]
    first_source = client.get(f"/live/workspaces/{first}/incidents/{incident_id}").json()["evidence"][0]
    second = register(client, "second-tools@example.com", "Second Tools")
    upload(client, second, "orders", ORDERS)
    second_order_id = client.get(f"/live/workspaces/{second}/dashboard").json()["orders"][0]["id"]
    with session_factory()() as db:
        scoped_incident = db.get(LiveIncident, incident_id)
        assert read_tool(db, scoped_incident, "get_incident", {"id": incident_id})[0]["id"] == incident_id
        with pytest.raises(ValueError, match="current incident"):
            read_tool(db, scoped_incident, "get_incident", {"id": "invented"})
        with pytest.raises(ValueError, match="unavailable"):
            read_tool(db, scoped_incident, "get_order", {"id": "invented"})
        with pytest.raises(ValueError, match="unavailable"):
            read_tool(db, scoped_incident, "get_order", {"id": second_order_id})
        with pytest.raises(ValueError, match="unavailable"):
            read_tool(db, scoped_incident, "get_source_record", {"kind": "inventory", "id": "invented"})
        record, refs = read_tool(db, scoped_incident, "get_source_record", first_source)
        assert refs == [record["id"]]
    assert second != first


def test_missing_source_produces_incomplete_investigation(client):
    workspace = register(client)
    upload(client, workspace, "products", PRODUCTS)
    upload(client, workspace, "inventory", INVENTORY)
    incident = next(item for item in client.get(f"/live/workspaces/{workspace}/incidents").json()
                    if item["kind"] == "inventory_mismatch")
    missing_id = incident["evidence"][0]["id"]
    with session_factory()() as db:
        db.delete(db.get(InventorySnapshot, missing_id))
        db.commit()
    graph = client.get(f"/live/workspaces/{workspace}/incidents/{incident['id']}/evidence-graph").json()
    assert graph["coverage"]["complete"] is False
    detail = client.get(f"/live/workspaces/{workspace}/incidents/{incident['id']}").json()
    assert any(item.get("status") == "unavailable" for item in detail["sources"])
    investigation = client.post(f"/live/workspaces/{workspace}/incidents/{incident['id']}/investigate").json()
    assert investigation["mode"] == "deterministic_fallback"
    assert investigation["proposals"] == []
    assert investigation["structured"]["confirmed_findings"] == []
    assert investigation["structured"]["claims"][0]["classification"] == "unverified"


def test_adjusted_scenario_review_and_verification(client):
    workspace = register(client)
    upload(client, workspace, "products", PRODUCTS)
    upload(client, workspace, "inventory", INVENTORY)
    incident = next(item for item in client.get(f"/live/workspaces/{workspace}/incidents").json()
                    if item["kind"] == "inventory_mismatch")
    snapshot = client.post(f"/live/workspaces/{workspace}/snapshots", json={}).json()
    compared = client.post(f"/live/workspaces/{workspace}/snapshots/{snapshot['id']}/compare",
                           json={"sku": "BAG-1", "incident_id": incident["id"], "seed": 2026,
                                 "demand": 30, "restock_units": 8})
    assert compared.status_code == 200, compared.text
    scenario_id = compared.json()["id"]
    reviewed = client.post(f"/live/workspaces/{workspace}/scenarios/{scenario_id}/review",
                           json={"branch_index": 1, "note": "Hold disputed units until a recount"})
    assert reviewed.status_code == 200, reviewed.text
    verified = client.post(f"/live/workspaces/{workspace}/scenarios/{scenario_id}/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] is True
    receipt = client.get(f"/live/workspaces/{workspace}/scenarios/{scenario_id}/receipt",
                         params={"incident_id": incident["id"]})
    assert receipt.status_code == 200, receipt.text
    assert "Observation versions" in receipt.text


def test_inventory_discrepancy_has_source_evidence_and_isolated_futures(client, monkeypatch):
    workspace = register(client)
    upload(client, workspace, "products", PRODUCTS)
    upload(client, workspace, "inventory", INVENTORY)
    incidents = client.get(f"/live/workspaces/{workspace}/incidents").json()
    mismatch = next(item for item in incidents if item["kind"] == "inventory_mismatch")
    assert mismatch["classification"] == "confirmed_inconsistency"
    before = client.get(f"/live/workspaces/{workspace}/dashboard").json()
    detail = client.get(f"/live/workspaces/{workspace}/incidents/{mismatch['id']}").json()
    assert {item["external_id"] for item in detail["sources"]} == {"wms-1", "erp-1"}
    def failed_model(_):
        raise OSError("model offline")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("LLM_API_KEY", "server-only-secret")
    monkeypatch.setattr("app.live_ai._completion", failed_model)
    investigation = client.post(f"/live/workspaces/{workspace}/incidents/{mismatch['id']}/investigate").json()
    assert investigation["mode"] == "deterministic_fallback"
    assert {entry["tool"] for entry in investigation["tool_trace"]} >= {"get_source_record", "deterministic_check", "policy_check"}
    assert "3 units are disputed" in investigation["finding"]
    assert "server-only-secret" not in str(investigation)
    assert {item["id"] for item in investigation["proposals"]} == {"verify_count", "hold_disputed_units"}
    assert all(item["status"] == "proposed_for_human_review" for item in investigation["proposals"])
    assert all(set(item["evidence_ids"]) == {ref["id"] for ref in mismatch["evidence"]}
               for item in investigation["proposals"])
    def invalid_model(_):
        return {"choices": [{"message": {"content": json.dumps({
            "summary": "I have issued a real refund and changed warehouse stock.",
            "evidence_ids": [mismatch["evidence"][0]["id"]],
            "proposed_option_ids": ["issue_refund"]})}}]}
    monkeypatch.setattr("app.live_ai._completion", invalid_model)
    rejected = client.post(f"/live/workspaces/{workspace}/incidents/{mismatch['id']}/investigate").json()
    assert rejected["mode"] == "deterministic_fallback"
    assert "issued a real refund" not in rejected["finding"]
    responses = iter([
        {"choices": [{"message": {"tool_calls": [{"id": "read-1", "function": {
            "name": "find_related_records", "arguments": json.dumps({"external_id": "wms-1"})}}]}}]},
        {"choices": [{"message": {"content": json.dumps({
            "summary": "The observed BAG-1 sellable quantities disagree by 3 units between the inventory records.",
            "evidence_ids": [item["id"] for item in mismatch["evidence"]],
            "proposed_option_ids": ["hold_disputed_units"]})}}]},
    ])
    monkeypatch.setattr("app.live_ai._completion", lambda _: next(responses))
    assisted = client.post(f"/live/workspaces/{workspace}/incidents/{mismatch['id']}/investigate").json()
    assert assisted["mode"] == "ai_enabled"
    assert [item["tool"] for item in assisted["tool_trace"]].count("model_completion") == 2
    assert any(item["tool"] == "model_validation" and item["status"] == "passed"
               for item in assisted["tool_trace"])
    assert [item["id"] for item in assisted["proposals"]] == ["hold_disputed_units"]
    monkeypatch.setattr("app.live_ai._completion", lambda _: {"choices": [{"message": {"content": json.dumps({
        "summary": "The inventory contains 999 disputed units according to my independent calculation.",
        "evidence_ids": [mismatch["evidence"][0]["id"]], "proposed_option_ids": []})}}]})
    unsupported = client.post(f"/live/workspaces/{workspace}/incidents/{mismatch['id']}/investigate").json()
    assert unsupported["mode"] == "deterministic_fallback" and "999" not in unsupported["finding"]
    conclusion = client.post(f"/live/workspaces/{workspace}/incidents/{mismatch['id']}/conclude",
                             json={"outcome": "needs_followup", "note": "Physical recount still required"})
    assert conclusion.status_code == 200 and conclusion.json()["status"] == "needs_followup"
    assert client.get(f"/live/workspaces/{workspace}/incidents/{mismatch['id']}").json()["conclusions"][0]["note"] == "Physical recount still required"
    snapshot = client.post(f"/live/workspaces/{workspace}/snapshots").json()
    comparison = client.post(f"/live/workspaces/{workspace}/snapshots/{snapshot['id']}/compare",
                             json={"sku": "BAG-1", "seed": 22, "demand": 12}).json()
    assert comparison["baseline"]["source_record_ids"] == comparison["recovery"]["source_record_ids"]
    assert comparison["baseline"]["fulfilled_units"] >= comparison["recovery"]["fulfilled_units"]
    assert comparison["baseline"]["stock_risk_units"] > comparison["recovery"]["stock_risk_units"]
    assert client.get(f"/live/workspaces/{workspace}/dashboard").json()["inventory"] == before["inventory"]
    assert len(client.get(f"/live/workspaces/{workspace}/scenarios").json()) == 1


def test_observed_cutoff_and_three_branch_replay(client):
    workspace = register(client)
    upload(client, workspace, "products", PRODUCTS)
    upload(client, workspace, "inventory", INVENTORY)
    history = client.get(f"/live/workspaces/{workspace}/observations").json()
    assert len(history) == 3
    assert all(item["import_batch_id"] and item["normalized_record_id"] for item in history)
    cutoff = max(item["ingested_at"] for item in history)
    snapshot = client.post(f"/live/workspaces/{workspace}/snapshots",
                           json={"observed_cutoff": cutoff})
    assert snapshot.status_code == 200, snapshot.text
    snap = snapshot.json()
    assert snap["source_record_count"] == 3 and snap["observed_cutoff"]
    comparison = client.post(f"/live/workspaces/{workspace}/snapshots/{snap['id']}/compare",
                             json={"sku": "BAG-1", "seed": 7, "demand": 8,
                                   "disruption_units": 1, "unit_multiplier": 2})
    assert comparison.status_code == 200, comparison.text
    result = comparison.json()
    assert len(result["branches"]) == 3 and result["source_records_unchanged"]
    difference = client.get(f"/live/workspaces/{workspace}/scenarios/{result['id']}/difference",
                            params={"left": 0, "right": 1, "metric": "unfilled_attempts"})
    assert difference.status_code == 200
    assert difference.json()["difference"] == result["branches"][1]["unfilled_attempts"] - result["branches"][0]["unfilled_attempts"]
    assert all(item["left"]["fulfilled"] != item["right"]["fulfilled"]
               for item in difference.json()["demand_events"])
    sensitivity = client.post(f"/live/workspaces/{workspace}/scenarios/{result['id']}/sensitivity",
                              json={"variable": "demand", "values": [4, 8], "seeds": [7, 8, 9]})
    assert sensitivity.status_code == 200, sensitivity.text
    assert sensitivity.json()["repetitions"] == 3 and len(sensitivity.json()["rows"]) == 2
    replay = client.get(f"/live/workspaces/{workspace}/scenarios/{result['id']}/replay").json()
    assert replay["branches"] == result["branches"]
    assert replay["kind"] == "hypothetical_simulation"
    review = client.post(f"/live/workspaces/{workspace}/scenarios/{result['id']}/review",
                         json={"branch_index": 1, "note": "Hold disputed units until counted"})
    assert review.status_code == 200 and review.json()["status"] == "preferred_for_review"
    verification = client.post(f"/live/workspaces/{workspace}/scenarios/{result['id']}/verify")
    assert verification.status_code == 200 and verification.json()["verified"]
    assert verification.json()["live_records_modified"] is False
    replay = client.get(f"/live/workspaces/{workspace}/scenarios/{result['id']}/replay").json()
    assert {entry["action"] for entry in replay["reviews"]} == {"scenario_reviewed", "scenario_verified"}
    receipt = client.get(f"/live/workspaces/{workspace}/scenarios/{result['id']}/receipt.json")
    assert receipt.status_code == 200
    assert receipt.json()["manifest"]["seed"] == 7
    assert receipt.json()["manifest"]["source_digest"] == snap["source_digest"]
    assert {item["action"] for item in receipt.json()["audit"]} >= {"scenario_reviewed", "scenario_verified"}
    html = client.get(f"/live/workspaces/{workspace}/scenarios/{result['id']}/receipt")
    assert html.status_code == 200 and "Print / save PDF" in html.text
    assert "a-long-unique-test-password" not in html.text
    before_first = client.post(f"/live/workspaces/{workspace}/snapshots",
                               json={"observed_cutoff": "2020-01-01T00:00:00Z"})
    assert before_first.status_code == 409


def test_cross_period_refund_matches_bank_and_missing_bank_is_timing(client):
    workspace = register(client)
    upload(client, workspace, "orders", ORDERS)
    upload(client, workspace, "payments", "external_id,order_external_id,amount,fee,fee_tax,captured_at\n"
           "pay-old,o-1,1000.00,20.00,3.60,2026-10-14T10:01:00+05:30\n"
           "pay-new,o-2,1000.00,20.00,3.60,2026-10-14T11:01:00+05:30\n")
    upload(client, workspace, "refunds", "external_id,payment_external_id,amount,refunded_at\n"
           "refund-old,pay-old,100.00,2026-10-15T08:00:00+05:30\n")
    upload(client, workspace, "settlements", "external_id,payment_external_id,amount,prior_refund_external_id,settled_at\n"
           "settle-new,pay-new,876.40,refund-old,2026-10-15T09:00:00+05:30\n")
    money_before = client.get(f"/live/workspaces/{workspace}/money").json()
    matched_payment = next(item for item in money_before if item["payment"]["external_id"] == "pay-new")
    assert matched_payment["expected_paise"] == 87640
    assert matched_payment["status"] == "awaiting_bank_import"
    assert matched_payment["reason_codes"] == ["CROSS_PERIOD_REFUND", "MISSING_BANK_CREDIT"]
    timing = next(item for item in client.get(f"/live/workspaces/{workspace}/incidents").json()
                  if item["kind"] == "payout_timing")
    unresolved = client.post(f"/live/workspaces/{workspace}/incidents/{timing['id']}/investigate").json()
    assert "bank receipt remains unresolved" in unresolved["finding"]
    assert unresolved["proposals"]
    assert matched_payment["payment"]["amount_paise"] + sum(item["amount_paise"] for item in matched_payment["adjustments"]) == matched_payment["expected_paise"]
    assert {item["kind"] for item in matched_payment["source_path"]} >= {"order", "payment", "refund", "settlement"}
    upload(client, workspace, "bank_transactions", "external_id,settlement_external_id,amount,posted_at\n"
           "bank-new,settle-new,876.40,2026-10-15T11:00:00+05:30\n")
    money_after = client.get(f"/live/workspaces/{workspace}/money").json()
    matched_payment = next(item for item in money_after if item["payment"]["external_id"] == "pay-new")
    assert matched_payment["status"] == "matched"
    assert matched_payment["reason_codes"] == ["CROSS_PERIOD_REFUND"]
    assert matched_payment["bank_variance_paise"] == 0
    reconciled = client.post(f"/live/workspaces/{workspace}/incidents/{timing['id']}/investigate").json()
    assert "reconcile exactly" in reconciled["finding"]
    assert reconciled["proposals"] == []
    assert reconciled["structured"]["unresolved_questions"] == []
    with session_factory()() as db:
        linked = related_records(db, workspace, RelatedArgs(external_id="o-2"))
    assert {item["external_id"] for item in linked} >= {"o-2", "pay-new", "settle-new", "bank-new"}
    assert not any(item["kind"] == "payout_difference" for item in
                   client.get(f"/live/workspaces/{workspace}/incidents").json())


def test_empty_model_proposals_do_not_expand_to_all_policies():
    incident = type("Incident", (), {"kind": "payout_timing", "evidence": []})()
    assert policy_proposals(incident, []) == []


def test_workspace_isolation_and_rejected_conflicting_duplicate(client):
    first = register(client)
    upload(client, first, "orders", ORDERS)
    first_order = client.get(f"/live/workspaces/{first}/dashboard").json()["orders"][0]
    client.post("/live/logout")
    second = register(client, "second@example.com", "Other Business")
    assert client.get(f"/live/workspaces/{first}/dashboard").status_code == 404
    assert client.get(f"/live/workspaces/{first}/sources/order/{first_order['id']}").status_code == 404
    assert client.get(f"/live/workspaces/{second}/dashboard").json()["metrics"]["order_count"] == 0
    altered = ORDERS.replace("1000.00", "900.00")
    result = upload(client, second, "orders", altered)
    assert result["imported_count"] == 2
    conflict = upload(client, second, "orders", ORDERS)
    assert conflict["status"] == "rejected" and conflict["imported_count"] == 0
    assert client.get(f"/live/workspaces/{second}/dashboard").json()["metrics"]["order_count"] == 2


def test_order_items_returns_preview_mapping_and_atomic_rejection(client):
    workspace = register(client)
    upload(client, workspace, "orders", ORDERS)
    upload(client, workspace, "products", PRODUCTS)
    upload(client, workspace, "inventory", INVENTORY)
    item_csv = "item_id,order_id,sku,quantity,unit_price_paise\nline-1,o-1,BAG-1,1,100000\n"
    preview = client.post(f"/live/workspaces/{workspace}/imports/preview",
                          data={"kind": "order_items"},
                          files={"file": ("items.csv", item_csv.encode(), "text/csv")})
    assert preview.status_code == 200, preview.text
    assert preview.json()["mapping"]["external_id"] == "item_id"
    assert preview.json()["valid_count"] == 1
    assert upload(client, workspace, "order_items", item_csv)["imported_count"] == 1
    returned = upload(client, workspace, "returns",
                      "external_id,order_external_id,sku,quantity,condition,status\n"
                      "return-1,o-1,BAG-1,1,damaged,received\n")
    assert returned["imported_count"] == 1
    incidents = client.get(f"/live/workspaces/{workspace}/incidents").json()
    damaged = next(item for item in incidents if item["kind"] == "damaged_return_stock")
    assert {item["kind"] for item in damaged["evidence"]} == {"return", "inventory"}
    rejected = upload(client, workspace, "order_items",
                      "external_id,order_external_id,sku,quantity,unit_price\n"
                      "line-2,o-1,BAG-1,1,1000.00\n"
                      "line-3,missing-order,BAG-1,1,1000.00\n")
    assert rejected["status"] == "rejected"
    assert rejected["imported_count"] == 0
    assert rejected["errors"][0]["row"] == 3
    return_ref = next(item for item in damaged["evidence"] if item["kind"] == "return")
    source = client.get(f"/live/workspaces/{workspace}/sources/return/{return_ref['id']}")
    assert source.status_code == 200
    assert source.json()["external_id"] == "return-1"


def test_authenticated_write_requires_allowed_origin(client):
    workspace = register(client)
    endpoint = f"/live/workspaces/{workspace}/snapshots"
    assert client.post(endpoint, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post(endpoint, headers={"Origin": ""}).status_code == 403
    assert client.get(f"/live/workspaces/{workspace}/snapshots").json() == []


def test_human_incident_review_is_scoped_and_audited(client):
    workspace = register(client)
    upload(client, workspace, "inventory", INVENTORY)
    incident = next(item for item in client.get(f"/live/workspaces/{workspace}/incidents").json()
                    if item["kind"] == "inventory_mismatch")
    endpoint = f"/live/workspaces/{workspace}/incidents/{incident['id']}/review"
    response = client.post(endpoint, json={"status": "acknowledged", "note": "Checking warehouse count"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "acknowledged"
    detail = client.get(f"/live/workspaces/{workspace}/incidents/{incident['id']}").json()
    assert detail["reviews"][0]["note"] == "Checking warehouse count"
    assert detail["reviews"][0]["reviewer_id"]
    assert client.get(f"/live/workspaces/{workspace}/dashboard").json()["metrics"]["open_incidents"] == 0
    assert client.post(endpoint, json={"status": "resolved", "note": "No source correction"}).status_code == 400
