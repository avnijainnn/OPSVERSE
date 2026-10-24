"""Reproducible, dependency-light OpsVerse commerce evaluation corpus.

Uses public Olist order/payment/delivery CSVs when provided, with no hidden
ground-truth claims about bank settlements or inventory. Synthetic mutations
have explicit labels and are NEVER presented as observed merchant facts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

KINDS = ("normal", "payment_under", "payment_over", "duplicate_payment",
         "missing_payment", "late_delivery", "missing_delivery",
         "unresolved_payment", "inventory_mismatch", "duplicate_inventory",
         "missing_inventory", "refund_without_payment")
LABELS = {
    "normal": "consistent",
    "payment_under": "payment_amount_mismatch",
    "payment_over": "payment_amount_mismatch",
    "duplicate_payment": "duplicate_payment",
    "missing_payment": "missing_payment",
    "late_delivery": "late_delivery",
    "missing_delivery": "missing_delivery",
    "unresolved_payment": "insufficient_evidence",
    "inventory_mismatch": "inventory_mismatch",
    "duplicate_inventory": "duplicate_inventory",
    "missing_inventory": "insufficient_evidence",
    "refund_without_payment": "orphan_refund",
}


def cents(value: str) -> int:
    return int((Decimal(value) * 100).quantize(Decimal("1")))


def olist_rows(root: Path) -> list[dict]:
    orders = root / "olist_orders_dataset.csv"
    payments = root / "olist_order_payments_dataset.csv"
    if not orders.exists() or not payments.exists():
        return []
    totals = defaultdict(int)
    with payments.open(newline="", encoding="utf-8") as f:
        for p in csv.DictReader(f):
            totals[p["order_id"]] += cents(p["payment_value"])
    rows = []
    with orders.open(newline="", encoding="utf-8") as f:
        for order in csv.DictReader(f):
            order_id = order["order_id"]
            if order_id not in totals:
                continue
            delivered = order.get("order_delivered_customer_date") or ""
            estimate = order.get("order_estimated_delivery_date") or ""
            rows.append({"source": "olist", "order_id": order_id,
                         "paid_cents": totals[order_id], "expected_cents": totals[order_id],
                         "delivery_date": delivered, "estimated_delivery": estimate})
    return rows


def generate(count: int, seed: int, rows: list[dict]) -> list[dict]:
    rng = random.Random(seed)
    result = []
    for i in range(count):
        source = dict(rows[i % len(rows)]) if rows else {
            "source": "synthetic", "order_id": f"synthetic-{i:06d}",
            "paid_cents": rng.randint(200, 100000),
            "expected_cents": 0, "delivery_date": "", "estimated_delivery": ""}
        if not rows:
            source["expected_cents"] = source["paid_cents"]
        kind = KINDS[i % len(KINDS)]
        case_id = f"case-{i:06d}"
        source["order_id"] = case_id
        expected = source["expected_cents"]
        source.update({"case_id": case_id, "fault": kind,
                       "payment_records": [{"id": case_id + "-pay", "amount_cents": expected}],
                       "inventory_records": [{"id": case_id + "-stock", "quantity": 10}],
                       "refund_records": [], "ground_truth": LABELS[kind],
                       "ground_truth_evidence_ids": [], "is_synthetic_mutation": True})
        if kind == "payment_under":
            source["payment_records"][0]["amount_cents"] = max(0, expected - 100)
        elif kind == "payment_over":
            source["payment_records"][0]["amount_cents"] = expected + 100
        elif kind == "duplicate_payment":
            source["payment_records"].append(dict(source["payment_records"][0],
                                                    id=case_id + "-pay-copy"))
        elif kind in ("missing_payment", "unresolved_payment"):
            source["payment_records"] = []
            if kind == "unresolved_payment":
                source["expected_cents"] = None
        elif kind == "late_delivery":
            source["delivery_date"] = "2026-01-05"
            source["estimated_delivery"] = "2026-01-01"
        elif kind == "missing_delivery":
            source["delivery_date"] = ""
            source["estimated_delivery"] = "2026-01-01"
        elif kind == "inventory_mismatch":
            source["inventory_records"].append({"id": case_id + "-stock-other", "quantity": 7})
        elif kind == "duplicate_inventory":
            source["inventory_records"].append(dict(source["inventory_records"][0],
                                                      id=case_id + "-stock-copy"))
        elif kind == "missing_inventory":
            source["inventory_records"] = []
        elif kind == "refund_without_payment":
            source["payment_records"] = []
            source["refund_records"].append({"id": case_id + "-refund", "amount_cents": 100})
        evidence = source["payment_records"] + source["inventory_records"] + source["refund_records"]
        source["ground_truth_evidence_ids"] = [e["id"] for e in evidence]
        result.append(source)
    rng.shuffle(result)
    return result


def evaluate(case: dict) -> dict:
    """Independent reference oracle for *injected* faults, not agent inference."""
    payments = case["payment_records"]
    inventory = case["inventory_records"]
    refunds = case["refund_records"]
    expected = case["expected_cents"]
    ids = [p["id"] for p in payments]
    if refunds and not payments:
        label = "orphan_refund"
        ids = [r["id"] for r in refunds]
    elif len(payments) > 1 and len({p["amount_cents"] for p in payments}) == 1:
        label = "duplicate_payment"
    elif not payments:
        label = "insufficient_evidence" if expected is None else "missing_payment"
    elif expected is not None and sum(p["amount_cents"] for p in payments) != expected:
        label = "payment_amount_mismatch"
    elif not inventory:
        label = "insufficient_evidence"
    elif len(inventory) > 1 and len({p["quantity"] for p in inventory}) == 1:
        label = "duplicate_inventory"
        ids = [x["id"] for x in inventory]
    elif len(inventory) > 1:
        label = "inventory_mismatch"
        ids = [x["id"] for x in inventory]
    elif case["delivery_date"] and case["estimated_delivery"] and case["delivery_date"] > case["estimated_delivery"]:
        label = "late_delivery"
    elif not case["delivery_date"] and case["estimated_delivery"]:
        label = "missing_delivery"
    else:
        label = "consistent"
    return {"label": label, "evidence_ids": ids}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--olist-dir", type=Path, default=Path("datasets/olist"))
    parser.add_argument("--count", type=int, default=1056)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("reports/offline_evaluation.json"))
    args = parser.parse_args()
    if not 12 <= args.count <= 100000:
        parser.error("count must be 12..100000")
    source = olist_rows(args.olist_dir)
    cases = generate(args.count, args.seed, source)
    correct = 0
    mismatches = []
    for case in cases:
        prediction = evaluate(case)
        if prediction["label"] == case["ground_truth"]:
            correct += 1
        else:
            mismatches.append({"case_id": case["case_id"], "expected": case["ground_truth"],
                               "predicted": prediction["label"]})
    report = {
        "suite": "OpsVerse offline injected-fault reference-oracle benchmark",
        "cases": len(cases), "correct": correct, "accuracy": correct / len(cases),
        "source": "olist" if source else "synthetic_only",
        "source_rows": len(source), "seed": args.seed,
        "source_note": "Olist supplies real orders/payments/delivery timestamps only. Fault labels are synthetic.",
        "agent_tested": False, "live_llm_tested": False,
        "live_provider_tested": False, "durability_tested": False,
        "mismatches": mismatches[:100],
        "case_hash": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
