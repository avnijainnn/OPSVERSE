"""Deterministic fixtures, incident investigation and sandbox recoveries."""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .agents import investigate
from .domain import metrics, project
from .store import append, create_run, events, rows, run

PRODUCTS = ["Canvas tote", "Weekender bag", "Crossbody bag", "Travel pouch", "Laptop sleeve",
            "Leather wallet", "Packing cubes", "Bottle sling", "Utility backpack", "Card holder"]
TYPES = {"damaged_return", "settlement_mismatch", "supplier_delay", "missing_payout"}


def seed_world(db: Any, seed: int = 2026, order_count: int = 18) -> str:
    if order_count < 18 or order_count > 300:
        raise ValueError("Demo fixture order count must be between 18 and 300")
    run_id = create_run(db, seed)
    rng = random.Random(seed)
    for supplier_number in range(1, 4):
        supplier_id = f"SUP-{supplier_number}"
        catalog = [f"SKU-{n+1:03d}" for n in range(40) if n % 3 == supplier_number-1]
        if supplier_number == 3:
            catalog.append("SKU-002")
        append(db, run_id, "SupplierConfigured", {"supplier_id": supplier_id,
            "lead_days": 2 if supplier_number == 3 else 3,
            "capacity": 50 if supplier_number == 3 else 40,
            "unit_cost_paise": 50000 if supplier_number == 3 else 40000,
            "catalog_skus": catalog}, "supplier", supplier_id)
    for n in range(40):
        sku = f"SKU-{n+1:03d}"
        name = PRODUCTS[n % len(PRODUCTS)] + (f" / {n//len(PRODUCTS)+1}" if n >= len(PRODUCTS) else "")
        append(db, run_id, "ProductStocked", {"sku": sku, "name": name,
            "price_paise": (799 + (n % 8) * 250) * 100, "quantity": 12 + rng.randrange(15),
            "supplier": f"SUP-{n%3+1}"}, "product", sku)
    for n in range(18):
        add_order(db, run_id, n + 1, rng, deliver=n < 13)
    seeded_state = project(events(db, run_id))
    for number in range(19, order_count + 1):
        delivered = number % 4 != 0
        created = add_order(db, run_id, number, rng, deliver=delivered, state=seeded_state)
        sku = created["payload"]["sku"]
        seeded_state["stock"][sku]["sellable"] -= 1
        if not delivered:
            seeded_state["stock"][sku]["reserved"] += 1
    for number in range(1, 4):
        payment_id = f"PAY-{number:04d}"
        payment = seeded_state["payments"][payment_id]
        expected = payment["captured_paise"] - payment["fee_paise"] - payment["fee_tax_paise"]
        capture_event = next(e for e in events(db, run_id) if e["event_type"] == "PaymentCaptured" and e["aggregate_id"] == payment_id)
        settlement_id = f"SET-SEED-{number:03d}"
        generated = append(db, run_id, "SettlementGenerated", {"settlement_id": settlement_id,
            "payment_id": payment_id, "prior_refund_paise": 0, "adjustment_id": None,
            "expected_paise": expected}, "settlement", settlement_id, capture_event["event_id"])
        append(db, run_id, "SettlementReceived", {"settlement_id": settlement_id,
            "amount_paise": expected}, "settlement", settlement_id, generated["event_id"])
    append(db, run_id, "OperationalExpenseRecorded", {"category": "warehouse_operations",
        "amount_paise": 165000}, "expense", "EXP-SEED-001")
    db.execute("UPDATE simulation_runs SET status='running' WHERE run_id=?", (run_id,))
    return run_id


def add_order(db: Any, run_id: str, number: int, rng: random.Random, deliver: bool = False,
              sku: str | None = None, state: dict[str, Any] | None = None) -> dict[str, Any]:
    sku = sku or f"SKU-{rng.randrange(40)+1:03d}"
    state = state if state is not None else project(events(db, run_id))
    if state["stock"][sku]["sellable"] < 1:
        sku = next((key for key, value in state["stock"].items() if value["sellable"] > 0), sku)
    order_id = f"ORD-{number:04d}"
    price = state["stock"][sku]["price_paise"]
    created = append(db, run_id, "OrderCreated", {"order_id": order_id, "sku": sku,
        "quantity": 1, "amount_paise": price,
        "channel": "Storefront" if number % 2 else "Marketplace"}, "order", order_id)
    append(db, run_id, "InventoryReserved", {"order_id": order_id}, "order", order_id, created["event_id"])
    append(db, run_id, "PaymentCaptured", {"payment_id": f"PAY-{number:04d}", "order_id": order_id,
        "amount_paise": price, "fee_paise": price * 2 // 100,
        "fee_tax_paise": (price * 2 // 100) * 18 // 100}, "payment", f"PAY-{number:04d}", created["event_id"])
    if deliver:
        append(db, run_id, "OrderDelivered", {"order_id": order_id}, "order", order_id, created["event_id"])
    return created


def step(db: Any, run_id: str) -> dict[str, Any]:
    existing = events(db, run_id)
    number = sum(e["event_type"] == "OrderCreated" for e in existing) + 1
    rng = random.Random(run(db, run_id)["seed"] + number * 7919)
    return add_order(db, run_id, number, rng, deliver=number % 4 != 0)


def incident(db: Any, incident_id: str) -> dict[str, Any]:
    result = rows(db, "SELECT * FROM incidents WHERE incident_id=?", (incident_id,))
    if not result:
        raise KeyError("Unknown incident")
    data = result[0]
    data["traces"] = rows(db, "SELECT * FROM agent_executions WHERE incident_id=? ORDER BY created_at", (incident_id,))
    for trace in data["traces"]:
        trace["tool_calls"] = rows(db, "SELECT * FROM agent_tool_calls WHERE execution_id=? ORDER BY created_at", (trace["execution_id"],))
    data["plans"] = rows(db, "SELECT * FROM recovery_plans WHERE incident_id=?", (incident_id,))
    data["scenario"] = rows(db, "SELECT * FROM scenario_results WHERE incident_id=? ORDER BY created_at DESC LIMIT 1", (incident_id,))
    data["verification"] = rows(db, "SELECT * FROM verification_results WHERE incident_id=?", (incident_id,))
    source_events = events(db, data["run_id"])
    data["evidence_events"] = [event for event in source_events if event["event_id"] in data["evidence_event_ids"]]
    return data


def inject(db: Any, run_id: str, kind: str) -> dict[str, Any]:
    if kind not in TYPES:
        raise ValueError("Unknown incident type")
    db.execute("UPDATE simulation_runs SET status='paused' WHERE run_id=?", (run_id,))
    state = project(events(db, run_id))
    evidence: list[str] = []
    if kind == "damaged_return":
        returned_orders = {r["order_id"] for r in state["returns"].values()}
        order_id = next((o for o, v in state["orders"].items() if v["status"] == "delivered" and o not in returned_orders), None)
        if not order_id:
            raise ValueError("No delivered order to return")
        return_id = f"RET-{uuid4().hex[:8]}"
        received = append(db, run_id, "ReturnReceived", {"return_id": return_id, "order_id": order_id}, "return", return_id)
        observed = append(db, run_id, "ExternalStockObserved", {"return_id": return_id,
            "claim": "sellable", "source": "faulty_returns_adapter"}, "return", return_id, received["event_id"])
        evidence = [received["event_id"], observed["event_id"]]
        affected = {"return_id": return_id, "order_id": order_id, "sku": state["orders"][order_id]["sku"]}
        description = "Returns integration claimed an uninspected bag was sellable. The ledger rejected the claim."
        root = "Premature external stock sync before quality inspection"
        action = "quarantine_return"
        severity = "high"
    elif kind in ("settlement_mismatch", "missing_payout"):
        order_id = f"ORD-{sum(e['event_type']=='OrderCreated' for e in events(db,run_id))+1:04d}"
        sku = "SKU-001"
        created = append(db, run_id, "OrderCreated", {"order_id": order_id, "sku": sku,
            "quantity": 1, "amount_paise": 100000, "channel": "Storefront"}, "order", order_id)
        payment_id = f"PAY-{uuid4().hex[:8]}"
        captured = append(db, run_id, "PaymentCaptured", {"payment_id": payment_id,
            "order_id": order_id, "amount_paise": 100000, "fee_paise": 2000,
            "fee_tax_paise": 360}, "payment", payment_id, created["event_id"])
        has_adjustment = kind == "settlement_mismatch"
        adjustment_id = f"ADJ-{uuid4().hex[:8]}" if has_adjustment else None
        refund = append(db, run_id, "PriorPeriodRefundRecorded", {"adjustment_id": adjustment_id,
            "amount_paise": 10000, "previous_settlement_id": "SET-PREVIOUS-CYCLE"},
            "refund", adjustment_id, captured["event_id"]) if adjustment_id else None
        expected_paise = 87640 if has_adjustment else 97640
        received_paise = 87640 if has_adjustment else 86640
        settlement_id = f"SET-{uuid4().hex[:8]}"
        generated = append(db, run_id, "SettlementGenerated", {"settlement_id": settlement_id,
            "payment_id": payment_id, "prior_refund_paise": 10000 if has_adjustment else 0,
            "expected_paise": expected_paise, "adjustment_id": adjustment_id}, "settlement", settlement_id,
            refund["event_id"] if refund else captured["event_id"])
        received = append(db, run_id, "SettlementReceived", {"settlement_id": settlement_id,
            "amount_paise": received_paise}, "settlement", settlement_id, generated["event_id"])
        naive = append(db, run_id, "ExternalMismatchObserved", {"settlement_id": settlement_id,
            "naive_expected_paise": 97640, "observed_paise": received_paise}, "settlement", settlement_id, received["event_id"])
        evidence = [captured["event_id"]] + ([refund["event_id"]] if refund else []) + [generated["event_id"], received["event_id"], naive["event_id"]]
        affected = {"order_id": order_id, "payment_id": payment_id, "settlement_id": settlement_id}
        description = ("Naive payout matching reports ₹100 missing; a prior-period refund may explain it."
                       if has_adjustment else "Bank credit is ₹110 short with no supported adjustment. The payout remains unresolved.")
        root = ("The external matcher omitted a linked prior-period refund adjustment"
                if has_adjustment else "Received bank credit is below the event-derived expected payout")
        action = "accept_reconciliation" if has_adjustment else "open_payout_dispute"
        severity = "medium" if has_adjustment else "high"
    else:
        sku = "SKU-002"
        delay = append(db, run_id, "SupplierDelayReported", {"supplier_id": "SUP-2",
            "sku": sku, "delay_days": 8}, "supplier", "SUP-2")
        surge = append(db, run_id, "DemandSurgeObserved", {"sku": sku,
            "projected_orders": 30, "horizon_days": 4}, "product", sku, delay["event_id"])
        evidence = [delay["event_id"], surge["event_id"]]
        affected = {"sku": sku, "supplier_id": "SUP-2"}
        description = "Flash-sale demand is projected to exceed sellable stock before a delayed delivery."
        root = "Eight-day supplier delay overlaps a four-day demand surge"
        action = "alternate_restock"
        severity = "high"
    incident_id = f"INC-{uuid4().hex[:8].upper()}"
    detected = append(db, run_id, "IncidentDetected", {"incident_id": incident_id, "type": kind},
                      "incident", incident_id, evidence[-1])
    now = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (incident_id, run_id, kind,
        severity, "awaiting_approval", detected["event_id"], __import__("json").dumps(evidence),
        __import__("json").dumps(affected), description, root, None, now))
    investigate(db, incident(db, incident_id))
    plan_id = str(uuid4())
    db.execute("INSERT INTO recovery_plans VALUES (?,?,?,?,?,?)", (plan_id, incident_id, action,
        __import__("json").dumps({"expected_sequence": detected["sequence_number"], "evidence_event_ids": evidence}),
        "low" if kind != "supplier_delay" else "medium", "proposed"))
    return incident(db, incident_id)


def _resolve(db: Any, run_id: str, incident_data: dict[str, Any], action: str) -> None:
    incident_id = incident_data["incident_id"]
    affected = incident_data["affected_entities"]
    if action == "quarantine_return":
        append(db, run_id, "ReturnInspected", {"return_id": affected["return_id"], "damaged": True},
               "return", affected["return_id"], incident_data["trigger_event_id"])
    elif action == "alternate_restock":
        ordered = append(db, run_id, "RestockOrdered", {"sku": affected["sku"],
            "quantity": 30, "supplier_id": "SUP-3", "lead_days": 2, "cost_paise": 1500000},
            "purchase_order", incident_id, incident_data["trigger_event_id"])
        append(db, run_id, "RestockReceived", {"sku": affected["sku"], "quantity": 30,
            "source_event_id": ordered["event_id"]}, "product", affected["sku"], ordered["event_id"],
            advance_minutes=2*24*60)
    elif action == "open_payout_dispute":
        append(db, run_id, "PayoutDisputeOpened", {"incident_id": incident_id,
            "settlement_id": affected["settlement_id"]}, "settlement", affected["settlement_id"],
            incident_data["trigger_event_id"])
        return
    elif action != "accept_reconciliation":
        raise ValueError("Unsupported action")
    append(db, run_id, "RecoveryVerified", {"incident_id": incident_id, "passed": True, **affected},
           "incident", incident_id, incident_data["trigger_event_id"])


def compare(db: Any, incident_id: str, manual_review_delay_hours: int = 24) -> dict[str, Any]:
    if not 0 <= manual_review_delay_hours <= 72:
        raise ValueError("Manual review delay must be 0–72 hours")
    data = incident(db, incident_id)
    parent = data["run_id"]
    source = run(db, parent)
    fork_sequence = len(events(db, parent))
    left = create_run(db, source["seed"], parent, fork_sequence)
    right = create_run(db, source["seed"], parent, fork_sequence)
    third = create_run(db, source["seed"], parent, fork_sequence)
    affected = data["affected_entities"]
    # Future inputs are identical in both branches; branch actions are separate.
    append(db, right, "ScenarioActionApplied", {"incident_id": incident_id,
        "action_type": data["plans"][0]["action_type"]}, "incident", incident_id)
    _resolve(db, right, data, data["plans"][0]["action_type"])
    append(db, third, "ScenarioActionApplied", {"incident_id": incident_id,
        "action_type": "defer_for_review"}, "incident", incident_id)
    append(db, third, "HorizonAdvanced", {"hours": manual_review_delay_hours, "reason": "manual_review"},
           "run", third, advance_minutes=manual_review_delay_hours * 60)
    _resolve(db, third, data, data["plans"][0]["action_type"])
    if data["type"] == "supplier_delay":
        append(db, left, "HorizonAdvanced", {"days": 2}, "run", left, advance_minutes=2*24*60+3)
        for branch in (left, right, third):
            for n in range(30):
                number = 10000 + n
                state = project(events(db, branch))
                if state["stock"][affected["sku"]]["sellable"] > 0:
                    add_order(db, branch, number, random.Random(source["seed"] + n), sku=affected["sku"])
                else:
                    append(db, branch, "DemandUnfilled", {"sku": affected["sku"], "attempt": number},
                           "product", affected["sku"])
    left_metrics = metrics(project(events(db, left)))
    right_metrics = metrics(project(events(db, right)))
    third_metrics = metrics(project(events(db, third)))
    result_id = str(uuid4())
    db.execute("INSERT INTO scenario_results (result_id, incident_id, baseline_run_id, recovery_run_id,"
        "baseline_metrics, recovery_metrics, created_at, third_run_id, third_metrics) VALUES (?,?,?,?,?,?,?,?,?)",
        (result_id, incident_id, left, right, __import__("json").dumps(left_metrics),
         __import__("json").dumps(right_metrics), datetime.now(timezone.utc).isoformat(),
         third, __import__("json").dumps(third_metrics)))
    return rows(db, "SELECT * FROM scenario_results WHERE result_id=?", (result_id,))[0]


def decide(db: Any, incident_id: str, approve: bool, expected_sequence: int) -> dict[str, Any]:
    data = incident(db, incident_id)
    if data["status"] != "awaiting_approval":
        raise ValueError("Incident is no longer awaiting approval")
    run_id = data["run_id"]
    if len(events(db, run_id)) != expected_sequence:
        raise ValueError("World changed; refresh before approval")
    plan = data["plans"][0]
    now = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO approvals VALUES (?,?,?,?,?)", (str(uuid4()), plan["plan_id"],
        "approved" if approve else "rejected", expected_sequence, now))
    append(db, run_id, "ActionApproved" if approve else "ActionRejected",
           {"incident_id": incident_id, "plan_id": plan["plan_id"]}, "incident", incident_id)
    if not approve:
        db.execute("UPDATE incidents SET status='rejected' WHERE incident_id=?", (incident_id,))
        db.execute("UPDATE recovery_plans SET status='rejected' WHERE plan_id=?", (plan["plan_id"],))
        return incident(db, incident_id)
    _resolve(db, run_id, data, plan["action_type"])
    state = project(events(db, run_id))
    checks = {"inventory_nonnegative": all(min(x["sellable"], x["reserved"], x["quarantined"], x["inspection_pending"]) >= 0 for x in state["stock"].values())}
    if data["type"] == "missing_payout":
        settlement_id = data["affected_entities"]["settlement_id"]
        checks["dispute_opened"] = settlement_id in state["payout_disputes"]
        checks["payout_still_unreconciled"] = not state["settlements"][settlement_id]["reconciled"]
    else:
        checks["incident_resolved"] = state["incidents"][incident_id]["status"] == "resolved"
        if data["type"] == "settlement_mismatch":
            checks["settlement_balanced"] = state["settlements"][data["affected_entities"]["settlement_id"]]["reconciled"]
        elif data["type"] == "damaged_return":
            checks["return_quarantined"] = state["returns"][data["affected_entities"]["return_id"]]["status"] == "quarantined"
        elif data["type"] == "supplier_delay":
            checks["restock_received"] = any(x["sku"] == data["affected_entities"]["sku"] and x["received"] for x in state["purchase_orders"].values())
    passed = all(checks.values())
    db.execute("INSERT INTO verification_results VALUES (?,?,?,?,?)", (str(uuid4()), incident_id,
        int(passed), __import__("json").dumps(checks), now))
    status = "investigating" if data["type"] == "missing_payout" and passed else "resolved" if passed else "failed"
    resolution = "Dispute opened; payout remains unreconciled" if data["type"] == "missing_payout" and passed else plan["action_type"] if passed else "Verification failed"
    db.execute("UPDATE incidents SET status=?,resolution=? WHERE incident_id=?",
               (status, resolution, incident_id))
    db.execute("UPDATE recovery_plans SET status=? WHERE plan_id=?", ("executed" if passed else "failed", plan["plan_id"]))
    return incident(db, incident_id)
