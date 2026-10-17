"""Append-only provenance for accepted source observations."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .live_models import Observation


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def normalized_state(record: Any) -> dict:
    """Capture values at ingestion; current mutable projections cannot serve historical replay."""
    return {column.name: (value.isoformat() if isinstance(value, datetime) else value)
            for column in record.__table__.columns
            if column.name != "raw" for value in [getattr(record, column.name)]}


def observe(db: Session, *, workspace_id: str, source_system: str, object_kind: str,
            provider_object_id: str, event_type: str, payload: dict,
            normalized_record_id: str | None = None, external_at: datetime | None = None,
            correlation_id: str | None = None, causation_id: str | None = None,
            import_batch_id: str | None = None, webhook_delivery_id: str | None = None,
            idempotency_key: str | None = None) -> tuple[Observation, bool]:
    digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
    key = idempotency_key or f"{object_kind}:{provider_object_id}:{digest}"
    prior_key = db.scalar(select(Observation).where(
        Observation.workspace_id == workspace_id,
        Observation.source_system == source_system,
        Observation.idempotency_key == key))
    if prior_key:
        return prior_key, False
    previous = db.scalar(select(Observation).where(
        Observation.workspace_id == workspace_id,
        Observation.source_system == source_system,
        Observation.object_kind == object_kind,
        Observation.provider_object_id == provider_object_id,
    ).order_by(Observation.ingested_at.desc(), Observation.id.desc()).limit(1))
    record = Observation(workspace_id=workspace_id, source_system=source_system,
                         object_kind=object_kind, provider_object_id=provider_object_id,
                         event_type=event_type, external_at=external_at,
                         correlation_id=correlation_id, causation_id=causation_id,
                         previous_observation_id=previous.id if previous else None,
                         idempotency_key=key, import_batch_id=import_batch_id,
                         webhook_delivery_id=webhook_delivery_id,
                         normalized_record_id=normalized_record_id, payload=payload,
                         ingested_at=datetime.now(timezone.utc))
    db.add(record)
    db.flush()
    return record, True


def upsert_source(db: Session, *, model: type, workspace_id: str, source_system: str,
                  object_kind: str, external_id: str, fields: dict, raw: dict,
                  external_at: datetime | None, event_type: str,
                  correlation_id: str | None = None,
                  webhook_delivery_id: str | None = None) -> tuple[Any, bool]:
    record = db.scalar(select(model).where(model.workspace_id == workspace_id,
                                           model.source_system == source_system,
                                           model.external_id == external_id))
    if record is not None and record.source_updated_at and external_at:
        previous_time = record.source_updated_at
        if previous_time.tzinfo is None:
            previous_time = previous_time.replace(tzinfo=timezone.utc)
        if external_at < previous_time:
            return record, False
    def equal(left: Any, right: Any) -> bool:
        if isinstance(left, datetime) and isinstance(right, datetime):
            aware = lambda value: value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
            return aware(left) == aware(right)
        return left == right
    changed = record is None or record.raw != raw or any(not equal(getattr(record, key), value)
                                                            for key, value in fields.items())
    if record is None:
        record = model(workspace_id=workspace_id, source_system=source_system,
                       external_id=external_id, raw=raw,
                       source_updated_at=external_at, **fields)
        db.add(record)
    elif changed:
        for key, value in fields.items():
            setattr(record, key, value)
        record.raw = raw
        record.source_updated_at = external_at
    if changed:
        db.flush()
        observe(db, workspace_id=workspace_id, source_system=source_system,
                object_kind=object_kind, provider_object_id=external_id,
                event_type=event_type, payload={"raw": raw, "normalized": normalized_state(record)},
                normalized_record_id=record.id, external_at=external_at,
                correlation_id=correlation_id, webhook_delivery_id=webhook_delivery_id)
    return record, changed
