"""Observed-state snapshots and deterministic, isolated branch replay."""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .live_models import Observation

KINDS = {"product": "products", "inventory": "inventory", "order": "orders",
         "order_item": "order_items", "return": "returns", "payment": "payments",
         "refund": "refunds", "settlement": "settlements", "bank_transaction": "bank_transactions"}
REQUIRED = {"products": {"sku", "name", "price_paise"},
            "inventory": {"sku", "location", "sellable", "reserved", "quarantined", "observed_at"},
            "orders": {"status", "total_paise"}}


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def freeze_state(db: Session, workspace_id: str, cutoff: datetime | None = None) -> tuple[dict, str]:
    """Reconstruct latest observed versions at the ingestion cutoff."""
    cutoff = utc(cutoff or datetime.now(timezone.utc))
    if cutoff > datetime.now(timezone.utc):
        raise ValueError("Snapshot timestamp cannot be in the future")
    history = db.scalars(select(Observation).where(Observation.workspace_id == workspace_id)
                         .order_by(Observation.ingested_at, Observation.id)).all()
    if not history:
        raise ValueError("No imported or synchronized observations exist in this workspace")
    first = utc(history[0].ingested_at)
    if cutoff < first:
        raise ValueError("The requested time precedes the first observed import or synchronization")
    latest: dict[tuple[str, str, str], Observation] = {}
    for observation in history:
        if utc(observation.ingested_at) <= cutoff and observation.object_kind in KINDS:
            latest[(observation.source_system, observation.object_kind,
                    observation.provider_object_id)] = observation
    state: dict = {name: [] for name in KINDS.values()}
    refs = []
    limitations = ["Observed history begins at first ingestion; earlier business state is unavailable"]
    for observation in latest.values():
        if observation.payload.get("deleted"):
            continue
        normalized = observation.payload.get("normalized")
        if not isinstance(normalized, dict):
            limitations.append("An older observation has no normalized version and was omitted")
            continue
        kind = KINDS[observation.object_kind]
        missing = REQUIRED.get(kind, set()) - normalized.keys()
        if missing:
            raise ValueError(f"Observed {kind} record lacks required fields: {', '.join(sorted(missing))}")
        row = {"id": normalized["id"], "source_id": observation.provider_object_id,
               "source_system": observation.source_system, "observation_id": observation.id,
               **{key: value for key, value in normalized.items()
                  if key not in {"id", "workspace_id", "import_batch_id", "source_system",
                                 "external_id", "recorded_at", "source_updated_at"}}}
        state[kind].append(row)
        refs.append({"observation_id": observation.id, "source": observation.source_system,
                     "kind": observation.object_kind, "external_id": observation.provider_object_id})
    if not state["products"] or not state["inventory"]:
        raise ValueError("Observed products and inventory are required for a digital twin")
    ids = {name: {item["id"] for item in rows} for name, rows in state.items()}
    for kind, parent, key in (("order_items", "orders", "order_id"),
                              ("returns", "orders", "order_id"),
                              ("payments", "orders", "order_id"),
                              ("refunds", "payments", "payment_id"),
                              ("settlements", "payments", "payment_id"),
                              ("bank_transactions", "settlements", "settlement_id")):
        for row in state[kind]:
            if row.get(key) and row[key] not in ids[parent]:
                raise ValueError(f"Observed {kind} record refers to a missing {parent} record")
    product_skus = {item["sku"] for item in state["products"]}
    if any(item["sku"] not in product_skus for item in state["inventory"]):
        raise ValueError("Observed inventory refers to a SKU without an observed product")
    for rows in state.values():
        rows.sort(key=lambda item: item["id"])
    state["schema_version"] = 2
    state["observed_cutoff"] = cutoff.isoformat()
    state["first_observed_at"] = first.isoformat()
    state["limitations"] = sorted(set(limitations))
    state["assumptions"] = ["No business events before ingestion are inferred"]
    state["source_refs"] = sorted(refs, key=lambda item: item["observation_id"])
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return state, hashlib.sha256(encoded).hexdigest()


def simulate_branches(state: dict, sku: str, seed: int, demand: int,
                      disruption_units: int = 0, unit_multiplier: int = 1,
                      *, supplier_lead_time: int = 0, restock_units: int = 0,
                      inspection_delay: int = 0, fulfillment_cost_paise: int = 0,
                      expedited_delivery_cost_paise: int = 0,
                      unfulfilled_cost_paise: int = 0,
                      return_processing_time: int = 0) -> list[dict]:
    if demand < 1 or demand > 500 or disruption_units < 0 or unit_multiplier < 1 or unit_multiplier > 5:
        raise ValueError("Demand, disruption, or estimated units per demand is outside allowed limits")
    if any(value < 0 or value > 100000 for value in (supplier_lead_time, restock_units,
           inspection_delay, fulfillment_cost_paise, expedited_delivery_cost_paise,
           unfulfilled_cost_paise, return_processing_time)):
        raise ValueError("Scenario assumptions are outside allowed limits")
    stock_rows = [item for item in state["inventory"] if item["sku"] == sku]
    products = [item for item in state["products"] if item["sku"] == sku]
    if not stock_rows or not products:
        raise ValueError("SKU is absent from this observed snapshot")
    latest_time = max(item["observed_at"] for item in stock_rows)
    latest = [item for item in stock_rows if item["observed_at"] == latest_time]
    highest = max(item["sellable"] for item in latest)
    lowest = min(item["sellable"] for item in latest)
    price = products[-1]["price_paise"]
    schedule = [random.Random(seed + n * 7919).randrange(1, 4) * unit_multiplier
                for n in range(demand)]
    plans = [("Use highest observed quantity", max(0, highest - disruption_units)),
             ("Hold disputed units", max(0, lowest - disruption_units)),
             ("Pause fulfillment for verification", 0)]

    def run(label: str, available: int, branch_index: int) -> dict:
        remaining = available
        fulfilled = 0
        cost = 0
        restocked = False
        trace = [{"step": 0, "event": "HypotheticalDisruption",
                  "disruption_units": disruption_units, "starting_sellable": available,
                  "classification": "simulated"}]
        for number, requested in enumerate(schedule, 1):
            if branch_index == 1 and restock_units and not restocked and number > supplier_lead_time:
                remaining += restock_units
                restocked = True
                trace.append({"step": number, "event": "HypotheticalRestock", "units": restock_units,
                              "remaining": remaining, "classification": "simulated"})
            if branch_index == 2 and inspection_delay and number > inspection_delay + return_processing_time and not restocked:
                remaining += max(0, lowest - disruption_units) + restock_units
                restocked = True
                trace.append({"step": number, "event": "HypotheticalInspectionRelease",
                              "units": remaining, "remaining": remaining, "classification": "simulated"})
            accepted = requested if remaining >= requested else 0
            remaining -= accepted
            fulfilled += accepted
            cost += accepted * fulfillment_cost_paise
            if accepted and restocked:
                cost += expedited_delivery_cost_paise
            if not accepted:
                cost += unfulfilled_cost_paise
            trace.append({"step": number, "event": "HypotheticalOrderFulfilled" if accepted else "HypotheticalDemandUnfilled",
                          "demand_id": f"demand-{number}", "requested": requested,
                          "fulfilled": accepted, "remaining": remaining, "classification": "simulated"})
        safe_capacity = max(0, lowest - disruption_units) + (restock_units if restocked else 0)
        risk_units = max(0, fulfilled - safe_capacity)
        return {"label": label, "starting_sellable": available, "fulfilled_units": fulfilled,
                "unfilled_attempts": sum(item["event"] == "HypotheticalDemandUnfilled" for item in trace),
                "hypothetical_revenue_paise": fulfilled * price,
                "stock_risk_units": risk_units,
                "ending_sellable": remaining, "additional_operational_cost_paise": cost,
                "refund_exposure_paise": risk_units * price,
                "orders_fulfilled": sum(item["event"] == "HypotheticalOrderFulfilled" for item in trace),
                "orders_delayed": sum(item["event"] == "HypotheticalDemandUnfilled" for item in trace),
                "policy_violations": risk_units if branch_index == 0 else 0,
                "recovery_step": next((item["step"] for item in trace if item["event"] == "HypotheticalOrderFulfilled"), None),
                "events": trace, "model_version": "inventory-v2",
                "source_record_ids": [item["id"] for item in latest]}

    return [run(label, quantity, index) for index, (label, quantity) in enumerate(plans)]


def explain_metric_difference(branches: list[dict], left: int, right: int, metric: str) -> dict:
    if left < 0 or right < 0 or left >= len(branches) or right >= len(branches):
        raise ValueError("Choose two saved branches")
    if metric not in {"unfilled_attempts", "fulfilled_units", "hypothetical_revenue_paise",
                      "additional_operational_cost_paise", "stock_risk_units"}:
        raise ValueError("Unsupported metric")
    a, b = branches[left], branches[right]
    by_demand = lambda branch: {event["demand_id"]: event for event in branch["events"] if "demand_id" in event}
    a_events, b_events = by_demand(a), by_demand(b)
    differences = [{"demand_id": key, "left": a_events[key], "right": b_events[key]}
                   for key in a_events.keys() & b_events.keys()
                   if a_events[key]["fulfilled"] != b_events[key]["fulfilled"]]
    differences.sort(key=lambda row: int(row["demand_id"].split("-")[1]))
    return {"metric": metric, "left_branch": left, "right_branch": right,
            "left_value": a[metric], "right_value": b[metric],
            "difference": b[metric] - a[metric], "demand_events": differences,
            "explanation": "Demand events show fulfillment differences; cost and risk also use the saved assumptions and branch policy"}


def compare_inventory(state: dict, sku: str, seed: int, demand: int) -> tuple[dict, dict]:
    branches = simulate_branches(state, sku, seed, demand)
    return branches[0], branches[1]
