"""Conserved, deterministic hourly counterfactual inventory simulation."""
from __future__ import annotations

import heapq
import random

METRICS = (
    "fulfilled_units", "unfulfilled_demand_units", "unfilled_attempts",
    "orders_fulfilled", "orders_partially_fulfilled", "orders_lost", "orders_delayed",
    "stock_risk_units", "hypothetical_revenue_paise",
    "additional_operational_cost_paise", "procurement_cost_paise",
    "expedited_shipping_cost_paise", "holding_cost_paise",
    "return_inspection_cost_paise", "recovery_cost_paise", "refund_exposure_paise",
)
LABELS = ("No intervention", "Conservative recovery", "Deterministic alternative recovery")


def simulate_branches(state: dict, sku: str, seed: int, demand: int,
                      disruption_units: int = 0, unit_multiplier: int = 1,
                      *, supplier_lead_time: int = 0, restock_units: int = 0,
                      inspection_delay: int = 0, fulfillment_cost_paise: int = 0,
                      expedited_delivery_cost_paise: int = 0,
                      unfulfilled_cost_paise: int = 0,
                      return_processing_time: int = 0,
                      inspection_result: str = "failed",
                      supplier_available: bool = True,
                      procurement_unit_cost_paise: int = 0,
                      holding_cost_paise_per_unit_hour: int = 0,
                      return_inspection_cost_paise: int = 0,
                      demand_schedule: list[dict] | None = None) -> list[dict]:
    """Replay identical demand in three isolated policies; all clock values are hours."""
    if not 1 <= demand <= 500 or disruption_units < 0 or not 1 <= unit_multiplier <= 5:
        raise ValueError("Demand, disruption, or units per demand is outside allowed limits")
    if inspection_result not in {"passed", "failed", "hold"}:
        raise ValueError("Inspection result must be passed, failed, or hold")
    if not isinstance(supplier_available, bool):
        raise ValueError("Supplier availability must be true or false")
    numeric = (supplier_lead_time, restock_units, inspection_delay, fulfillment_cost_paise,
               expedited_delivery_cost_paise, unfulfilled_cost_paise, return_processing_time,
               procurement_unit_cost_paise, holding_cost_paise_per_unit_hour,
               return_inspection_cost_paise)
    if any(not isinstance(value, int) or value < 0 or value > 100000 for value in numeric):
        raise ValueError("Scenario assumptions are outside allowed limits")
    rows = [row for row in state["inventory"] if row["sku"] == sku]
    products = [row for row in state["products"] if row["sku"] == sku]
    if not rows or not products:
        raise ValueError("SKU is absent from this observed snapshot")
    locations: dict[str, list[dict]] = {}
    for row in rows:
        locations.setdefault(str(row.get("location", "")), []).append(row)
    latest = []
    low = high = existing_reserved = existing_quarantined = 0
    for location_rows in locations.values():
        latest_at = max(row["observed_at"] for row in location_rows)
        current = [row for row in location_rows if row["observed_at"] == latest_at]
        latest.extend(current)
        low += min(int(row["sellable"]) for row in current)
        high += max(int(row["sellable"]) for row in current)
        existing_reserved += min(int(row.get("reserved", 0)) for row in current)
        existing_quarantined += min(int(row.get("quarantined", 0)) for row in current)
    verified = max(0, low - disruption_units)
    disputed = max(0, high - low - max(0, disruption_units - low))
    price = int(products[-1]["price_paise"])
    clock = 0
    arrivals = []
    if demand_schedule is not None:
        if len(demand_schedule) != demand:
            raise ValueError("Modeled demand count does not match the supplied schedule")
        for number, row in enumerate(demand_schedule, 1):
            at, units = row.get("time_hours"), row.get("units")
            if (not isinstance(at, int) or not isinstance(units, int) or
                    at < 1 or at > 168 or units < 1 or units > 10000 or at < clock):
                raise ValueError("Modeled demand schedule is invalid")
            clock = at
            arrivals.append((at, number, units * unit_multiplier))
    else:
        for number in range(1, demand + 1):
            rng = random.Random(seed + number * 7919)
            clock += 1 + rng.randrange(4)
            arrivals.append((clock, number, rng.randrange(1, 4) * unit_multiplier))
    horizon = max(24, arrivals[-1][0] + 1)

    def run(index: int) -> dict:
        buckets = {"verified_sellable": verified, "disputed": disputed, "reserved": existing_reserved,
                   "quarantined": existing_quarantined, "damaged": 0, "ordered_inbound": 0,
                   "received_inbound": 0, "fulfilled": 0}
        totals = {metric: 0 for metric in METRICS}
        events: list[dict] = []
        queue: list[tuple[int, int, int, str, dict]] = []
        serial = 0
        received = 0
        last_cost_hour = 0
        inspection_state = "PENDING" if index else "QUARANTINED"
        recovery_at = None
        delivery_at = None
        po_id = f"po-{seed}-{index}"

        def enqueue(at: int, priority: int, kind: str, **payload: object) -> None:
            nonlocal serial
            serial += 1
            heapq.heappush(queue, (at, priority, serial, kind, payload))

        def emit(at: int, kind: str, before: dict | None = None,
                 contributions: dict | None = None, **payload: object) -> None:
            changes = {key: value for key, value in (contributions or {}).items() if value}
            for key, value in changes.items():
                totals[key] += value
            events.append({"event_id": f"branch-{index}-event-{len(events) + 1}",
                           "step": len(events), "time_hours": at, "event": kind,
                           "classification": "simulated", "policy": LABELS[index],
                           "state_before": before if before is not None else buckets.copy(),
                           "state_after": buckets.copy(), "metric_contributions": changes,
                           **payload})

        def invariant() -> None:
            if any(value < 0 for value in buckets.values()):
                raise AssertionError("Negative simulation inventory")
            physical = sum(buckets[key] for key in (
                "verified_sellable", "disputed", "reserved", "quarantined", "damaged", "fulfilled"))
            if physical != verified + disputed + existing_reserved + existing_quarantined + received:
                raise AssertionError("Simulation inventory was created or lost")
            if buckets["received_inbound"] != received:
                raise AssertionError("Supplier receipt was duplicated")

        emit(0, "HypotheticalDisruption", disruption_units=disruption_units,
             starting_sellable=verified + (disputed if index == 0 else 0),
             source_record_ids=[row["id"] for row in latest])
        if index:
            if inspection_result == "hold":
                before = buckets.copy()
                buckets["quarantined"] += buckets["disputed"]
                buckets["disputed"] = 0
                inspection_state = "QUARANTINED"
                emit(0, "HypotheticalIndefiniteHold", before, units=disputed,
                     inspection_state=inspection_state)
            else:
                enqueue(inspection_delay, 1, "inspection_start")
                enqueue(inspection_delay + return_processing_time, 2, "inspection_end")
        if index and restock_units and not supplier_available:
            emit(0, "HypotheticalSupplierUnavailable", units=restock_units,
                 reason="Selected replacement supplier is unavailable")
        if index and restock_units and supplier_available:
            lead = supplier_lead_time if index == 1 else (supplier_lead_time + 1) // 2
            delivery_at = lead
            before = buckets.copy()
            buckets["ordered_inbound"] += restock_units
            procurement = restock_units * procurement_unit_cost_paise
            expedite = expedited_delivery_cost_paise if index == 2 else 0
            emit(0, "HypotheticalSupplierOrder", before, {
                "procurement_cost_paise": procurement,
                "expedited_shipping_cost_paise": expedite,
                "recovery_cost_paise": procurement + expedite,
                "additional_operational_cost_paise": procurement + expedite},
                purchase_order_id=po_id, units=restock_units, expected_delivery_hour=lead)
            enqueue(lead // 4, 3, "dispatch")
            enqueue(lead, 4, "delivery")
        for at, number, requested in arrivals:
            enqueue(at, 6, "order", number=number, requested=requested)
        if index and inspection_result != "hold" and (not restock_units or supplier_available):
            completion = max(inspection_delay + return_processing_time, delivery_at or 0)
            if completion <= horizon:
                enqueue(completion, 8, "recovery")

        while queue:
            at, _, _, kind, payload = heapq.heappop(queue)
            if at > horizon:
                continue
            if holding_cost_paise_per_unit_hour and at > last_cost_hour:
                holding = buckets["verified_sellable"] * (at - last_cost_hour) * holding_cost_paise_per_unit_hour
                emit(at, "HypotheticalHoldingCost", contributions={
                    "holding_cost_paise": holding, "additional_operational_cost_paise": holding},
                    interval_start_hour=last_cost_hour, interval_end_hour=at)
            last_cost_hour = at
            before = buckets.copy()
            if kind == "inspection_start":
                inspection_state = "IN_PROGRESS"
                emit(at, "HypotheticalInspectionStarted", before,
                     inspection_state=inspection_state, units=disputed)
            elif kind == "inspection_end":
                inspection_state = "PASSED" if inspection_result == "passed" else "FAILED"
                inspection_cost = disputed * return_inspection_cost_paise
                emit(at, "HypotheticalInspectionResult", before, {
                    "return_inspection_cost_paise": inspection_cost,
                    "recovery_cost_paise": inspection_cost,
                    "additional_operational_cost_paise": inspection_cost},
                    inspection_state=inspection_state, units=disputed)
                before = buckets.copy()
                if inspection_result == "passed":
                    buckets["verified_sellable"] += buckets["disputed"]
                    buckets["disputed"] = 0
                    inspection_state = "RELEASED"
                    emit(at, "HypotheticalInspectionRelease", before,
                         inspection_state=inspection_state, units=disputed)
                else:
                    buckets["quarantined"] += buckets["disputed"]
                    buckets["disputed"] = 0
                    inspection_state = "QUARANTINED"
                    emit(at, "HypotheticalQuarantine", before,
                         inspection_state=inspection_state, units=disputed)
            elif kind == "dispatch":
                emit(at, "HypotheticalSupplierDispatch", before,
                     purchase_order_id=po_id, units=restock_units)
            elif kind == "delivery":
                emit(at, "HypotheticalSupplierDelivery", before,
                     purchase_order_id=po_id, units=restock_units,
                     expected_delivery_hour=delivery_at, actual_delivery_hour=at)
                before = buckets.copy()
                if buckets["ordered_inbound"] < restock_units:
                    raise AssertionError("Duplicate or unordered supplier receipt")
                buckets["ordered_inbound"] -= restock_units
                buckets["verified_sellable"] += restock_units
                buckets["received_inbound"] += restock_units
                received += restock_units
                emit(at, "HypotheticalStockReceipt", before,
                     purchase_order_id=po_id, units=restock_units)
            elif kind == "order":
                requested = int(payload["requested"])
                verified_taken = min(requested, buckets["verified_sellable"])
                # Conservative recovery avoids splitting orders. The alternative
                # policy allocates confirmed partial quantities when supply is tight.
                if index == 1 and verified_taken < requested:
                    verified_taken = 0
                speculative_taken = (min(requested - verified_taken, buckets["disputed"])
                                     if index == 0 else 0)
                accepted = verified_taken + speculative_taken
                buckets["verified_sellable"] -= verified_taken
                buckets["disputed"] -= speculative_taken
                buckets["reserved"] += accepted
                demand_id = f"demand-{payload['number']}"
                emit(at, "HypotheticalReservation", before, demand_id=demand_id,
                     requested=requested, reserved=accepted,
                     disputed_reserved=speculative_taken)
                before = buckets.copy()
                buckets["reserved"] -= accepted
                buckets["fulfilled"] += accepted
                lost = requested - accepted
                costs = accepted * fulfillment_cost_paise + int(lost > 0) * unfulfilled_cost_paise
                outcome = ("HypotheticalOrderFulfilled" if accepted == requested else
                           "HypotheticalOrderPartiallyFulfilled" if accepted else
                           "HypotheticalDemandUnfilled")
                emit(at, outcome, before, {
                    "fulfilled_units": accepted, "unfulfilled_demand_units": lost,
                    "unfilled_attempts": int(lost > 0),
                    "orders_fulfilled": int(accepted == requested),
                    "orders_partially_fulfilled": int(0 < accepted < requested),
                    "orders_lost": int(lost > 0), "stock_risk_units": speculative_taken,
                    "hypothetical_revenue_paise": accepted * price,
                    "additional_operational_cost_paise": costs},
                    demand_id=demand_id, requested=requested, fulfilled=accepted,
                    lost_units=lost, disputed_fulfilled=speculative_taken,
                    remaining=buckets["verified_sellable"] + (buckets["disputed"] if index == 0 else 0))
            elif kind == "recovery":
                if inspection_state in {"RELEASED", "QUARANTINED"} and not buckets["ordered_inbound"]:
                    recovery_at = at
                    emit(at, "HypotheticalRecoveryCompleted", before,
                         inspection_state=inspection_state, unresolved_disputed_units=0)
            invariant()

        if holding_cost_paise_per_unit_hour and horizon > last_cost_hour:
            holding = buckets["verified_sellable"] * (horizon - last_cost_hour) * holding_cost_paise_per_unit_hour
            emit(horizon, "HypotheticalHoldingCost", contributions={
                "holding_cost_paise": holding, "additional_operational_cost_paise": holding},
                interval_start_hour=last_cost_hour, interval_end_hour=horizon)
        invariant()
        return {"label": LABELS[index], "policy_origin": "deterministic",
                "starting_sellable": verified + (disputed if index == 0 else 0),
                "ending_sellable": buckets["verified_sellable"] + (buckets["disputed"] if index == 0 else 0),
                **totals, "potential_refund_exposure_paise": totals["stock_risk_units"] * price,
                "realized_simulated_contribution_paise": None,
                "confirmed_inventory_violations": 0, "policy_violations": totals["stock_risk_units"],
                "recovery_step": None, "incident_detected_at": 0,
                "recovery_started_at": 0 if index else None,
                "recovery_completed_at": recovery_at, "recovery_duration": recovery_at,
                "recovery_status": "completed" if recovery_at is not None else "unresolved",
                "inspection_state": inspection_state, "inventory": buckets,
                "horizon_hours": horizon, "clock_unit": "hours_since_incident_detection",
                "demand_schedule": [{"demand_id": f"demand-{number}",
                                     "time_hours": at, "units": units}
                                    for at, number, units in arrivals],
                "events": events, "model_version": "inventory-v3-event-clock",
                "source_record_ids": [row["id"] for row in latest]}

    return [run(index) for index in range(3)]


def explain_metric_difference(branches: list[dict], left: int, right: int, metric: str) -> dict:
    if not 0 <= left < len(branches) or not 0 <= right < len(branches):
        raise ValueError("Choose two saved branches")
    if metric not in METRICS:
        raise ValueError("Unsupported metric")
    a, b = branches[left], branches[right]
    if not all(event.get("event_id") for branch in (a, b) for event in branch["events"]):
        if metric not in a or metric not in b:
            raise ValueError("This legacy run did not save the requested metric")
        return {"metric": metric, "left_branch": left, "right_branch": right,
                "left_value": a[metric], "right_value": b[metric],
                "difference": b[metric] - a[metric], "demand_events": [],
                "left_contributions": [], "right_contributions": [],
                "attribution_status": "unavailable_legacy",
                "explanation": "This older run did not save event-level metric contributions; rerun from its snapshot for attribution."}

    def contributions(branch: dict) -> list[dict]:
        return [{"event_id": event["event_id"], "event": event["event"],
                 "time_hours": event["time_hours"], "demand_id": event.get("demand_id"),
                 "value": event.get("metric_contributions", {}).get(metric, 0),
                 "state_before": event["state_before"], "state_after": event["state_after"]}
                for event in branch["events"] if event.get("metric_contributions", {}).get(metric)]

    left_rows, right_rows = contributions(a), contributions(b)
    if sum(row["value"] for row in left_rows) != a[metric] or sum(row["value"] for row in right_rows) != b[metric]:
        raise ValueError("Saved events do not reconcile with the metric")
    demand_events = []
    left_demands = {event["demand_id"]: event for event in a["events"]
                    if "demand_id" in event and "fulfilled" in event}
    right_demands = {event["demand_id"]: event for event in b["events"]
                     if "demand_id" in event and "fulfilled" in event}
    for demand in a.get("demand_schedule", []):
        key = demand["demand_id"]
        if key in left_demands and key in right_demands and left_demands[key]["fulfilled"] != right_demands[key]["fulfilled"]:
            demand_events.append({"demand_id": key, "left": left_demands[key], "right": right_demands[key]})
    return {"metric": metric, "left_branch": left, "right_branch": right,
            "left_value": a[metric], "right_value": b[metric],
            "difference": b[metric] - a[metric], "demand_events": demand_events,
            "left_contributions": left_rows, "right_contributions": right_rows,
            "attribution_status": "reconciled",
            "explanation": "Each side is the exact sum of its saved event contributions."}


def find_first_branch_divergence(branches: list[dict]) -> dict | None:
    if len(branches) < 2:
        return None
    demand_maps = [{event["demand_id"]: event for event in branch["events"]
                    if "demand_id" in event and "fulfilled" in event} for branch in branches]
    for demand in branches[0].get("demand_schedule", []):
        key = demand["demand_id"]
        events = [mapping.get(key) for mapping in demand_maps]
        if any(event is None for event in events):
            continue
        decisions = {(event["fulfilled"], event.get("disputed_fulfilled", 0)) for event in events}
        if len(decisions) > 1:
            return {"demand_id": key, "time_hours": demand["time_hours"],
                    "events": events,
                    "reason": "Policies fulfilled different quantities or used disputed stock"}
    # When allocations agree throughout, the first changed stock state still
    # matters (for example, a supplier receipt that changes future capacity).
    for demand in branches[0].get("demand_schedule", []):
        key = demand["demand_id"]
        events = [mapping.get(key) for mapping in demand_maps]
        if any(event is None for event in events):
            continue
        if len({tuple(sorted(event["state_after"].items())) for event in events}) > 1:
            return {"demand_id": key, "time_hours": demand["time_hours"],
                    "events": events, "reason": "Policies reached different inventory states"}
    return None
