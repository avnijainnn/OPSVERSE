"""Commerce-specialist tool routing and conservative incident correlation.

Correlation links possibly related incidents; it never asserts shared root cause.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .live_models import LiveIncident

SPECIALISTS = {
    "inventory": {
        "kinds": {"inventory_mismatch", "damaged_return_stock", "fulfillment_failure"},
        "focus": "Inventory counts, order lines, delayed suppliers and return classification.",
        "tools": ("trace_inventory", "get_order", "get_return", "find_related_records", "get_source_coverage"),
    },
    "payments": {
        "kinds": {"refund_inconsistency", "payout_difference", "payout_timing", "unmatched_bank_credit"},
        "focus": "Payment-refund-settlement-bank chronology, integer paise, fees and timing.",
        "tools": ("reconcile_settlement", "find_related_records", "get_source_record", "get_source_coverage"),
    },
    "provenance": {
        "kinds": {"duplicate_source_conflict"},
        "focus": "Duplicate imported IDs, conflicting versions and missing source coverage.",
        "tools": ("get_source_record", "find_related_records", "get_source_coverage"),
    },
}


def specialist(incident: LiveIncident) -> dict[str, Any]:
    for name, config in SPECIALISTS.items():
        if incident.kind in config["kinds"]:
            return {"name": name, "focus": config["focus"], "preferred_tools": list(config["tools"])}
    return {"name": "general", "focus": "Verify evidence and abstain on unknown causes.",
            "preferred_tools": ["get_incident", "get_source_record", "get_source_coverage"]}


def _entities(incident: LiveIncident) -> set[str]:
    refs = incident.evidence or []
    values: set[str] = set()
    for item in refs:
        if not isinstance(item, dict):
            continue
        for key in ("id", "external_id"):
            value = str(item.get(key) or "").strip()
            if value:
                values.add(key + ":" + value)
    return values


def correlate(db: Session, incident: LiveIncident, limit: int = 5) -> list[dict[str, Any]]:
    """Rank probable incident peers by overlapping source references, not by LLM claims."""
    anchor = _entities(incident)
    if not anchor:
        return []
    peers = db.scalars(select(LiveIncident).where(
        LiveIncident.workspace_id == incident.workspace_id,
        LiveIncident.id != incident.id,
    ).order_by(LiveIncident.created_at.desc()).limit(250)).all()
    output = []
    for peer in peers:
        shared = sorted(anchor.intersection(_entities(peer)))
        if shared:
            output.append({"incident_id": peer.id, "kind": peer.kind,
                           "shared_evidence_keys": shared[:8], "overlap_count": len(shared),
                           "relationship": "source_overlap_not_confirmed_causality"})
    return sorted(output, key=lambda x: (-x["overlap_count"], x["incident_id"]))[:limit]


def gaps(failed_tools: list[dict], *, initial_missing: bool = False) -> list[str]:
    missing = ["An incident source record was unavailable"] if initial_missing else []
    for step in failed_tools:
        if step.get("status") != "passed":
            missing.append("Evidence query " + str(step.get("read_tool", "unknown")) + " was unavailable")
    return list(dict.fromkeys(missing))[:5]
