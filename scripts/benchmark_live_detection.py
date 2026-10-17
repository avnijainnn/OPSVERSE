"""Evaluate live detection against hand-labeled CSV workspaces.

Set POSTGRES_TEST_URL to use disposable PostgreSQL; otherwise a temporary SQLite
database is used. Run from the repository root.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
import sys
sys.path.insert(0, str(API))
from app.main import app  # noqa: E402
from app.live_models import engine  # noqa: E402

PRODUCT = "external_id,sku,name,price\np1,BAG-1,Canvas bag,1000.00\n"
INVENTORY_HEAD = "external_id,sku,location,sellable,reserved,quarantined,observed_at\n"
ORDER_HEAD = "external_id,customer_ref,status,channel,total,placed_at\n"
PAYMENT = "external_id,order_external_id,amount,fee,fee_tax,captured_at\npay1,o1,1000.00,20.00,3.00,2026-10-15T09:01:00Z\n"
SETTLEMENT_HEAD = "external_id,payment_external_id,amount,settled_at\n"
BANK_HEAD = "external_id,settlement_external_id,amount,posted_at\n"
RETURN_HEAD = "external_id,order_external_id,sku,quantity,condition,status\n"
REFUND_HEAD = "external_id,payment_external_id,amount,refunded_at\n"
FULFILLED_ORDER = ORDER_HEAD + "o1,buyer-a,fulfilled,Storefront,1000.00,2026-10-15T09:00:00Z\n"
STOCK = INVENTORY_HEAD + "wms,BAG-1,Main,5,0,0,2026-10-15T09:00:00Z\n"

CASES = [
    {"name": "same-time_inventory_disagreement", "label": True,
     "kind": "inventory_mismatch", "evidence": {"wms", "erp"},
     "imports": [("products", PRODUCT), ("inventory", INVENTORY_HEAD +
                  "wms,BAG-1,Main,5,0,0,2026-10-15T09:00:00Z\n"
                  "erp,BAG-1,Main,2,0,0,2026-10-15T09:00:00Z\n")]},
    {"name": "same-time_inventory_agreement", "label": False,
     "imports": [("products", PRODUCT), ("inventory", INVENTORY_HEAD +
                  "wms,BAG-1,Main,5,0,0,2026-10-15T09:00:00Z\n"
                  "erp,BAG-1,Main,5,0,0,2026-10-15T09:00:00Z\n")]},
    {"name": "failed_fulfillment", "label": True,
     "kind": "fulfillment_failure", "evidence": {"o1"},
     "imports": [("orders", ORDER_HEAD +
                  "o1,buyer-a,failed,Storefront,1000.00,2026-10-15T09:00:00Z\n")]},
    {"name": "completed_fulfillment", "label": False,
     "imports": [("orders", ORDER_HEAD +
                  "o1,buyer-a,fulfilled,Storefront,1000.00,2026-10-15T09:00:00Z\n")]},
    {"name": "payout_difference", "label": True,
     "kind": "payout_difference", "evidence": {"pay1", "settle1"},
     "money_status": "provider_difference", "bank_paise": 97000, "imports": [
         ("orders", ORDER_HEAD + "o1,buyer-a,fulfilled,Storefront,1000.00,2026-10-15T09:00:00Z\n"),
         ("payments", PAYMENT),
         ("settlements", SETTLEMENT_HEAD + "settle1,pay1,970.00,2026-10-16T09:00:00Z\n"),
         ("bank_transactions", BANK_HEAD + "bank1,settle1,970.00,2026-10-16T12:00:00Z\n")]},
    {"name": "payout_balanced", "label": False,
     "money_status": "matched", "bank_paise": 97700, "imports": [
         ("orders", ORDER_HEAD + "o1,buyer-a,fulfilled,Storefront,1000.00,2026-10-15T09:00:00Z\n"),
         ("payments", PAYMENT),
         ("settlements", SETTLEMENT_HEAD + "settle1,pay1,977.00,2026-10-16T09:00:00Z\n"),
         ("bank_transactions", BANK_HEAD + "bank1,settle1,977.00,2026-10-16T12:00:00Z\n")]},
]

CASES.extend([
    {"name": "different_time_inventory_change", "label": False,
     "imports": [("products", PRODUCT), ("inventory", INVENTORY_HEAD +
                  "wms,BAG-1,Main,5,0,0,2026-10-15T09:00:00Z\n"
                  "erp,BAG-1,Main,2,0,0,2026-10-15T10:00:00Z\n")]},
    {"name": "different_location_inventory", "label": False,
     "imports": [("products", PRODUCT), ("inventory", INVENTORY_HEAD +
                  "wms,BAG-1,Main,5,0,0,2026-10-15T09:00:00Z\n"
                  "erp,BAG-1,Secondary,2,0,0,2026-10-15T09:00:00Z\n")]},
    {"name": "damaged_return_not_quarantined", "label": True,
     "kind": "damaged_return_stock", "evidence": {"r1", "wms"},
     "imports": [("products", PRODUCT), ("orders", FULFILLED_ORDER),
                 ("inventory", STOCK),
                 ("returns", RETURN_HEAD + "r1,o1,BAG-1,1,damaged,received\n")]},
    {"name": "damaged_return_quarantined", "label": False,
     "imports": [("products", PRODUCT), ("orders", FULFILLED_ORDER),
                 ("inventory", INVENTORY_HEAD + "wms,BAG-1,Main,5,0,1,2026-10-15T09:00:00Z\n"),
                 ("returns", RETURN_HEAD + "r1,o1,BAG-1,1,damaged,received\n")]},
    {"name": "refund_exceeds_capture", "label": True,
     "kind": "refund_inconsistency", "evidence": {"pay1", "refund1"},
     "imports": [("orders", FULFILLED_ORDER), ("payments", PAYMENT),
                 ("refunds", REFUND_HEAD + "refund1,pay1,1100.00,2026-10-16T09:00:00Z\n")]},
    {"name": "partial_refund_within_capture", "label": False,
     "imports": [("orders", FULFILLED_ORDER), ("payments", PAYMENT),
                 ("refunds", REFUND_HEAD + "refund1,pay1,100.00,2026-10-16T09:00:00Z\n")]},
    {"name": "bank_credit_difference", "label": True,
     "kind": "payout_difference", "evidence": {"pay1", "settle1", "bank1"},
     "money_status": "bank_difference", "expected_paise": 97700, "bank_paise": 97000,
     "imports": [("orders", FULFILLED_ORDER), ("payments", PAYMENT),
                 ("settlements", SETTLEMENT_HEAD + "settle1,pay1,977.00,2026-10-16T09:00:00Z\n"),
                 ("bank_transactions", BANK_HEAD + "bank1,settle1,970.00,2026-10-16T12:00:00Z\n")]},
    {"name": "balanced_settlement_awaiting_bank", "label": False,
     "money_status": "awaiting_bank_import", "expected_paise": 97700, "bank_paise": 0,
     "imports": [("orders", FULFILLED_ORDER), ("payments", PAYMENT),
                 ("settlements", SETTLEMENT_HEAD + "settle1,pay1,977.00,2026-10-16T09:00:00Z\n")]},
    {"name": "unmatched_bank_credit", "label": True,
     "kind": "unmatched_bank_credit", "evidence": {"bank-orphan"},
     "imports": [("bank_transactions", BANK_HEAD + "bank-orphan,,500.00,2026-10-16T12:00:00Z\n")]},
    {"name": "split_bank_credit_balanced", "label": False,
     "money_status": "matched", "expected_paise": 97700, "bank_paise": 97700,
     "imports": [("orders", FULFILLED_ORDER), ("payments", PAYMENT),
                 ("settlements", SETTLEMENT_HEAD + "settle1,pay1,977.00,2026-10-16T09:00:00Z\n"),
                 ("bank_transactions", BANK_HEAD +
                  "bank1,settle1,500.00,2026-10-16T12:00:00Z\n"
                  "bank2,settle1,477.00,2026-10-16T12:01:00Z\n")]},
])


def evaluate(url: str) -> dict:
    os.environ["DATABASE_URL"] = url
    os.environ["SESSION_SECRET"] = "live-benchmark-only-session-secret-with-32-chars"
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "migrations"))
    command.upgrade(config, "head")
    outcomes = []
    with TestClient(app, headers={"Origin": "http://localhost:3000"}) as client:
        for case in CASES:
            registered = client.post("/live/register", json={
                "email": f"bench-{uuid4().hex}@example.test",
                "password": "unique-labeled-fixture-password",
                "workspace_name": f"Benchmark {case['name']}"})
            assert registered.status_code == 200, registered.text
            workspace = registered.json()["workspaces"][0]["id"]
            for kind, csv in case["imports"]:
                response = client.post(f"/live/workspaces/{workspace}/imports",
                                       data={"kind": kind}, files={"file":
                                       (f"{kind}.csv", csv.encode(), "text/csv")})
                assert 200 <= response.status_code < 300 and response.json()["status"] == "complete", f"{response.status_code} {response.text}"
            incidents = client.get(f"/live/workspaces/{workspace}/incidents").json()
            open_incidents = [item for item in incidents if item["status"] == "open" and
                              item["classification"] != "explained_timing_difference"]
            cause_correct = False
            if case["label"]:
                cause_correct = any(item["kind"] == case["kind"] and
                                    case["evidence"].issubset({ref["external_id"] for ref in item["evidence"]})
                                    for item in open_incidents)
            finance_correct = None
            if "money_status" in case:
                money = client.get(f"/live/workspaces/{workspace}/money").json()
                finance_correct = (len(money) == 1 and money[0]["expected_paise"] ==
                                   case.get("expected_paise", 97700) and
                                   money[0]["bank_total_paise"] == case["bank_paise"] and
                                   money[0]["status"] == case["money_status"])
            outcomes.append({"name": case["name"], "labeled_incident": case["label"],
                             "predicted_incident": bool(open_incidents),
                             "open_incident_kinds": [item["kind"] for item in open_incidents],
                             "root_cause_and_evidence_correct": cause_correct if case["label"] else None,
                             "financial_reconciliation_correct": finance_correct})
    tp = sum(row["labeled_incident"] and row["predicted_incident"] for row in outcomes)
    fp = sum(not row["labeled_incident"] and row["predicted_incident"] for row in outcomes)
    fn = sum(row["labeled_incident"] and not row["predicted_incident"] for row in outcomes)
    finance = [row["financial_reconciliation_correct"] for row in outcomes if row["financial_reconciliation_correct"] is not None]
    return {"evaluated_at": datetime.now(timezone.utc).isoformat(),
            "configuration": {"mode": "live_csv_api", "database": make_url(url).get_backend_name(),
                              "unit": "hand-labeled workspace fixture"},
            "sample_size": len(outcomes), "positive_cases": sum(row["labeled_incident"] for row in outcomes),
            "negative_cases": sum(not row["labeled_incident"] for row in outcomes),
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "detection_precision": tp / (tp + fp) if tp + fp else None,
            "detection_recall": tp / (tp + fn) if tp + fn else None,
            "root_cause_accuracy": sum(row["root_cause_and_evidence_correct"] is True for row in outcomes) /
                                   sum(row["labeled_incident"] for row in outcomes),
            "financial_reconciliation_accuracy": sum(finance) / len(finance),
            "financial_cases": len(finance),
            "limitations": (f"{len(outcomes)} hand-labeled CSV workspaces covering inventory, fulfillment, returns, refunds and payouts. "
                            "Explained payout timing is excluded from actionable-incident labels. "
                            "Rates check known rules on curated fixtures, not merchant-population or model accuracy."),
            "cases": outcomes}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "live_detection_fixtures.json")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        url = os.environ.get("POSTGRES_TEST_URL") or f"sqlite:///{Path(folder) / 'live_benchmark.db'}"
        try:
            report = evaluate(url)
        finally:
            engine().dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = json.dumps(report, indent=2)
    args.output.write_text(output, encoding="utf-8")
    print(args.output)
    if args.output.resolve() == ROOT / "reports" / "live_detection_fixtures.json":
        browser_report = ROOT / "apps" / "web" / "public" / "live_detection_fixtures.json"
        browser_report.write_text(output, encoding="utf-8")
        print(browser_report)
