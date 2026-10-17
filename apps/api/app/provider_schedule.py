"""Claim and run due read-only provider syncs from an external scheduler."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from .live_models import ProviderConnection, session_factory
from .live_providers import mark_error
from .razorpay_api import sync_provider
from .shopify_sync import sync_shopify


def sync_due(*, interval_minutes: int = 15, limit: int = 10,
             provider: str | None = None) -> list[dict]:
    if not 1 <= interval_minutes <= 1440 or not 1 <= limit <= 100:
        raise ValueError("Choose a 1-1440 minute interval and a 1-100 connection batch")
    if provider not in (None, "shopify", "razorpay"):
        raise ValueError("Choose shopify or razorpay")
    now = datetime.now(timezone.utc)
    due_at = now - timedelta(minutes=interval_minutes)
    stale_at = now - timedelta(minutes=max(30, interval_minutes * 2))
    results = []
    for _ in range(limit):
        with session_factory()() as db:
            query = (select(ProviderConnection).where(
                ProviderConnection.provider.in_([provider] if provider else ["shopify", "razorpay"]),
                ProviderConnection.access_ciphertext.is_not(None),
                ProviderConnection.refresh_ciphertext.is_not(None),
                or_(and_(ProviderConnection.status == "connected",
                         or_(ProviderConnection.last_sync_at.is_(None),
                             ProviderConnection.last_sync_at <= due_at)),
                    and_(ProviderConnection.status == "error", ProviderConnection.updated_at <= due_at),
                    and_(ProviderConnection.status == "syncing", ProviderConnection.updated_at <= stale_at)))
                .order_by(ProviderConnection.updated_at)
                .limit(1)
                .with_for_update(skip_locked=True))
            record = db.scalar(query)
            if record is None:
                break
            record.status = "syncing"
            record.updated_at = now
            connection_id = record.id
            workspace_id = record.workspace_id
            provider_name = record.provider
            db.commit()  # The status is the durable lease; another scheduler skips it.
            try:
                result = sync_shopify(db, record) if provider_name == "shopify" else sync_provider(db, record)
                results.append({"provider": provider_name, "workspace_id": workspace_id,
                                "status": "connected", "counts": result["counts"]})
            except Exception as exc:
                db.rollback()
                record = db.get(ProviderConnection, connection_id)
                mark_error(record, exc)
                db.commit()
                results.append({"provider": provider_name, "workspace_id": workspace_id,
                                "status": "error"})
    return results
