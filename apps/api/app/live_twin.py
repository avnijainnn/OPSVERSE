"""Observed-state snapshots and deterministic, isolated branch replay."""
from __future__ import annotations

import hashlib
import json
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


from .live_simulation import simulate_branches, explain_metric_difference, find_first_branch_divergence


def compare_inventory(state: dict, sku: str, seed: int, demand: int) -> tuple[dict, dict]:
    branches = simulate_branches(state, sku, seed, demand)
    return branches[0], branches[1]
