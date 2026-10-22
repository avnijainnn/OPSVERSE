"""Conservative, reproducible demand input from observed SKU order history."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone


VERSION = "sku-seven-day-rate-v1"


def demand_model(state: dict, sku: str, horizon_hours: int = 24) -> dict:
    if not 1 <= horizon_hours <= 168:
        raise ValueError("Demand horizon must be between 1 and 168 hours")
    cutoff = datetime.fromisoformat(state["observed_cutoff"])
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    start = cutoff - timedelta(days=7)
    orders = {row["id"]: row for row in state.get("orders", [])}
    examples = []
    for item in state.get("order_items", []):
        if item.get("sku") != sku or item.get("order_id") not in orders:
            continue
        order = orders[item["order_id"]]
        if str(order.get("status", "")).lower() in {"cancelled", "canceled", "voided"}:
            continue
        placed = datetime.fromisoformat(order["placed_at"])
        if placed.tzinfo is None:
            placed = placed.replace(tzinfo=timezone.utc)
        quantity = int(item["quantity"])
        if start <= placed <= cutoff and quantity > 0:
            examples.append((placed, order["id"], item["id"], quantity))
    grouped: dict[str, dict] = {}
    for placed, order_id, item_id, quantity in examples:
        row = grouped.setdefault(order_id, {"placed": placed, "units": 0, "items": []})
        row["units"] += quantity
        row["items"].append(item_id)
    sample = sorted(grouped.values(), key=lambda row: row["placed"])
    count = len(sample)
    active_days = len({row["placed"].date() for row in sample})
    oldest_hours = (cutoff - sample[0]["placed"]).total_seconds() / 3600 if sample else 0
    latest_hours = (cutoff - sample[-1]["placed"]).total_seconds() / 3600 if sample else None
    forecast = round(count * horizon_hours / 168)
    reasons = []
    if count < 8 or active_days < 3 or oldest_hours < 72:
        reasons.append("At least 8 SKU orders across 3 days with 72 hours of span are required")
    if count > 1000:
        reasons.append("Observed SKU history exceeds the supported 1000-order model limit")
    if any(row["units"] > 10000 for row in sample):
        reasons.append("A source order exceeds the supported modeled quantity")
    if latest_hours is None or latest_hours > 72:
        reasons.append("The latest SKU order must be within 72 hours of the snapshot")
    if forecast < 1 or forecast > 500:
        reasons.append("The projected demand must be between 1 and 500 attempts")
    return {"status": "ready" if not reasons else "insufficient_history",
            "model_version": VERSION, "sku": sku, "horizon_hours": horizon_hours,
            "forecast_attempts": forecast if not reasons else None,
            "sample_orders": count, "active_days": active_days,
            "latest_order_age_hours": round(latest_hours, 1) if latest_hours is not None else None,
            "source_order_ids": sorted(grouped) if count <= 1000 else [],
            "source_order_item_ids": sorted(item_id for row in sample for item_id in row["items"]) if count <= 1000 else [],
            "observed_units_per_order": [row["units"] for row in sample] if count <= 1000 else [],
            "message": "; ".join(reasons) if reasons else
                       "Uses the last seven days of observed SKU orders; forecast is an estimate, not a validated prediction"}


def modeled_schedule(model: dict, seed: int) -> list[dict]:
    if model["status"] != "ready":
        raise ValueError(model["message"])
    rng = random.Random(seed)
    count = model["forecast_attempts"]
    horizon = model["horizon_hours"]
    units = model["observed_units_per_order"]
    # Stratification prevents a single random draw from moving all demand to one hour.
    return [{"time_hours": min(horizon, max(1, int((number - 1 + rng.random()) * horizon / count) + 1)),
             "units": units[rng.randrange(len(units))]}
            for number in range(1, count + 1)]
