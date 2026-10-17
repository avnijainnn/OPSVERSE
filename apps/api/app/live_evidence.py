"""Workspace-scoped, provenance-aware incident evidence graph."""
from __future__ import annotations

from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .live_ai import KINDS
from .live_models import LiveIncident, Observation


def iso(value):
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else
            value.astimezone(timezone.utc)).isoformat()


def evidence_graph(db: Session, incident: LiveIncident) -> dict:
    """No guessed records or causal edges: each link has an explicit basis."""
    workspace_id = incident.workspace_id
    nodes = [{"id": f"incident:{incident.id}", "kind": "incident", "label": incident.kind,
              "classification": "derived", "summary": incident.summary,
              "occurred_at": iso(incident.created_at)}]
    edges = []
    timeline = []
    missing = []
    source_records = []
    for ref in incident.evidence:
        kind, record_id = ref.get("kind"), ref.get("id")
        model = KINDS.get(kind)
        record = db.get(model, record_id) if model and record_id else None
        if record is None or record.workspace_id != workspace_id:
            missing.append({"kind": kind, "id": record_id, "reason": "Referenced source record is unavailable"})
            continue
        source_id = f"source:{kind}:{record_id}"
        source_records.append((kind, record))
        nodes.append({"id": source_id, "kind": kind, "record_id": record_id,
                      "label": getattr(record, "external_id", None) or getattr(record, "filename", record_id),
                      "classification": "observed", "source_system": getattr(record, "source_system", "csv"),
                      "import_batch_id": getattr(record, "import_batch_id", None),
                      "source_updated_at": iso(getattr(record, "source_updated_at", None))})
        edges.append({"id": f"supports:{source_id}", "source": source_id,
                      "target": f"incident:{incident.id}", "relation": "used_by_detection",
                      "classification": "derived", "basis": "Incident detector stored this source reference"})
        versions = db.scalars(select(Observation).where(
            Observation.workspace_id == workspace_id,
            Observation.object_kind == kind,
            Observation.normalized_record_id == record_id)
            .order_by(Observation.ingested_at.desc(), Observation.id.desc()).limit(101)).all()
        if len(versions) > 100:
            missing.append({"kind": kind, "id": record_id,
                            "reason": "Graph shows only the 100 newest observation versions"})
            versions = versions[:100]
        versions.reverse()
        if not versions:
            missing.append({"kind": kind, "id": record_id,
                            "reason": "No observation version is linked to this normalized record"})
        for version in versions:
            observation_id = f"observation:{version.id}"
            nodes.append({"id": observation_id, "kind": "observation", "record_id": version.id,
                          "label": version.event_type, "classification": "observed",
                          "source_system": version.source_system,
                          "external_at": iso(version.external_at), "ingested_at": iso(version.ingested_at),
                          "schema_version": version.schema_version,
                          "import_batch_id": version.import_batch_id,
                          "webhook_delivery_id": version.webhook_delivery_id})
            edges.append({"id": f"version:{version.id}", "source": observation_id,
                          "target": source_id, "relation": "observation_version",
                          "classification": "observed", "basis": "normalized_record_id"})
            timeline.append({"id": version.id, "kind": kind, "event_type": version.event_type,
                             "source_system": version.source_system,
                             "external_at": iso(version.external_at), "ingested_at": iso(version.ingested_at),
                             "record_id": record_id})
    for index, (kind, record) in enumerate(source_records):
        for other_kind, other in source_records[index + 1:]:
            relation = None
            basis = None
            for field in ("order_id", "payment_id", "settlement_id", "prior_refund_id"):
                if getattr(record, field, None) == other.id or getattr(other, field, None) == record.id:
                    relation, basis = "explicit_reference", field
                    break
            if relation is None and kind == other_kind == "inventory" and record.sku == other.sku and record.location == other.location:
                relation, basis = "same_sku_location", "sku and location; causal relationship unverified"
            if relation:
                edges.append({"id": f"related:{record.id}:{other.id}",
                              "source": f"source:{kind}:{record.id}",
                              "target": f"source:{other_kind}:{other.id}",
                              "relation": relation, "classification": "observed" if relation == "explicit_reference" else "inferred",
                              "basis": basis})
    timeline.sort(key=lambda row: (row["ingested_at"], row["id"]))
    return {"incident_id": incident.id, "nodes": nodes, "edges": edges,
            "timeline": timeline, "missing_evidence": missing,
            "coverage": {"source_references": len(incident.evidence),
                         "resolved_sources": len(source_records),
                         "observation_versions": len(timeline),
                         "complete": not missing},
            "limitations": ["Detector links establish input provenance, not physical causality",
                            "Observed history begins at first ingestion"]}
