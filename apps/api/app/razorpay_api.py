"""Read-only Razorpay polling adapter with encrypted merchant API credentials."""
from __future__ import annotations

import os
import re
import time
import hashlib
import hmac
import json
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .live_auth import current_user, get_db, same_origin, workspace_member, require_workspace_role
from .live_detection import detect_workspace
from .live_models import (AuditRecord, BankTransaction, Observation, Order, Payment,
                          ProviderConnection, Refund, Settlement, User, WebhookDelivery, session_factory)
from .live_observations import observe, upsert_source
from .live_providers import account_available, cipher, connection, decrypt, encrypt, mark_error, public_status

router = APIRouter(prefix="/live/workspaces/{workspace_id}/razorpay")
webhook_router = APIRouter(prefix="/live/razorpay")
KEY_PATTERN = re.compile(r"^rzp_(test|live)_[A-Za-z0-9]{8,80}$")
WEBHOOK_TOPICS = {"payment.captured", "refund.processed", "settlement.processed"}


class RazorpayError(RuntimeError):
    pass


class Credentials(BaseModel):
    key_id: str = Field(min_length=15, max_length=100)
    key_secret: str = Field(min_length=16, max_length=200)


class WebhookSecret(BaseModel):
    secret: str = Field(min_length=16, max_length=200)


def status_payload(record: ProviderConnection | None) -> dict:
    result = public_status(record, configured=bool(os.getenv("CREDENTIAL_ENCRYPTION_KEY")))
    result["webhook_configured"] = bool(record and record.webhook_secret_ciphertext)
    result["webhook_path"] = (f"/live/razorpay/webhooks/{record.id}"
                              if record and record.access_ciphertext else None)
    return result


def request_get(key_id: str, key_secret: str, path: str, params: dict) -> dict:
    if path not in {"payments", "refunds", "settlements", "settlements/recon/combined"}:
        raise RazorpayError("Unsupported read endpoint")
    for attempt in range(3):
        try:
            with httpx.Client(timeout=httpx.Timeout(12, connect=4), follow_redirects=False,
                              auth=(key_id, key_secret)) as client:
                response = client.get("https://api.razorpay.com/v1/" + path, params=params)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(0.3 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise RazorpayError(f"Razorpay returned HTTP {response.status_code}")
            result = response.json()
            if not isinstance(result, dict) or result.get("entity") != "collection" or not isinstance(result.get("items"), list):
                raise RazorpayError("Razorpay returned an invalid collection")
            return result
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == 2:
                raise RazorpayError("Razorpay request failed after retries") from exc
            time.sleep(0.2 * (attempt + 1))
    raise RazorpayError("Razorpay request failed")


def pages(key_id: str, key_secret: str, path: str, params: dict):
    for page in range(100):
        result = request_get(key_id, key_secret, path, {**params, "count": 100, "skip": page * 100})
        items = result["items"]
        yield items
        if len(items) < 100:
            return
    raise RazorpayError(f"Razorpay {path} exceeded the configured 10,000-record sync limit")


def time_pages(key_id: str, key_secret: str, path: str, params: dict, depth: int = 0):
    """Split dense inclusive time windows rather than silently truncating history."""
    start, end = params["from"], params["to"]
    try:
        buffered = list(pages(key_id, key_secret, path, params))
    except RazorpayError as exc:
        if "exceeded the configured 10,000-record sync limit" not in str(exc):
            raise
        if depth >= 32 or start >= end:
            raise RazorpayError(f"Razorpay {path} has more than 10,000 records in one second") from exc
        middle = (start + end) // 2
        yield from time_pages(key_id, key_secret, path, {**params, "to": middle}, depth + 1)
        yield from time_pages(key_id, key_secret, path, {**params, "from": middle + 1}, depth + 1)
    else:
        yield from buffered


def integer(item: dict, name: str) -> int:
    value = item.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RazorpayError(f"Razorpay returned an invalid {name}")
    return value


def event_time(value: int) -> datetime:
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, TypeError, ValueError) as exc:
        raise RazorpayError("Razorpay returned an invalid event time") from exc


def safe_payment(item: dict) -> dict:
    return {key: item.get(key) for key in ("id", "order_id", "amount", "currency", "status", "captured",
                                           "fee", "tax", "created_at")}


def safe_refund(item: dict) -> dict:
    return {key: item.get(key) for key in ("id", "payment_id", "amount", "currency", "status", "created_at")}


def safe_settlement(item: dict) -> dict:
    return {key: item.get(key) for key in ("id", "amount", "status", "fees", "tax", "utr", "created_at")}


def safe_recon(item: dict) -> dict:
    return {key: item.get(key) for key in ("entity_id", "type", "debit", "credit", "amount", "currency",
                                           "fee", "tax", "created_at", "settled_at", "settlement_id",
                                           "payment_id", "settlement_utr")}


def sync_provider(db: Session, record: ProviderConnection, *, from_time_floor: int | None = None,
                  webhook_delivery_id: str | None = None) -> dict:
    key_id = decrypt(record.access_ciphertext)
    key_secret = decrypt(record.refresh_ciphertext)
    now = datetime.now(timezone.utc)
    previous = record.last_sync_at
    if previous and previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    from_time = int((previous - timedelta(minutes=5)).timestamp()) if previous else 946684800
    if from_time_floor is not None:
        from_time = min(from_time, max(946684800, from_time_floor))
    period = {"from": from_time, "to": int(now.timestamp())}
    workspace = record.workspace_id
    counts = {"payments": 0, "refunds": 0, "settlements": 0, "recon_lines": 0}
    limitations: set[str] = set()
    for items in time_pages(key_id, key_secret, "payments", period):
        for item in items:
            if item.get("currency") != "INR":
                limitations.add("Non-INR payments were skipped; currency conversion is not configured")
                continue
            if item.get("status") != "captured" or not item.get("captured"):
                continue
            if item.get("fee") is None or item.get("tax") is None:
                limitations.add("A captured payment lacks fee or tax details and was skipped")
                continue
            order = db.scalar(select(Order).where(Order.workspace_id == workspace,
                                                  Order.external_id == item.get("order_id"))) if item.get("order_id") else None
            if item.get("order_id") and order is None:
                limitations.add("Some Razorpay order IDs could not be linked to observed commerce orders")
            upsert_source(db, model=Payment, workspace_id=workspace, source_system="razorpay",
                          object_kind="payment", external_id=item["id"], event_type="razorpay_payment_observed",
                          fields={"order_id": order.id if order else None, "amount_paise": integer(item, "amount"),
                                  "fee_paise": integer(item, "fee"), "fee_tax_paise": integer(item, "tax"),
                                  "captured_at": event_time(integer(item, "created_at"))},
                          raw=safe_payment(item), external_at=event_time(integer(item, "created_at")),
                          correlation_id=item.get("order_id"), webhook_delivery_id=webhook_delivery_id)
            counts["payments"] += 1
    for items in time_pages(key_id, key_secret, "refunds", period):
        for item in items:
            if item.get("currency") != "INR":
                limitations.add("Non-INR refunds were skipped")
                continue
            payment = db.scalar(select(Payment).where(Payment.workspace_id == workspace,
                                                      Payment.source_system == "razorpay",
                                                      Payment.external_id == item.get("payment_id")))
            if not payment:
                limitations.add("A refund has no observed captured payment and was skipped")
                continue
            upsert_source(db, model=Refund, workspace_id=workspace, source_system="razorpay",
                          object_kind="refund", external_id=item["id"], event_type="razorpay_refund_observed",
                          fields={"payment_id": payment.id, "order_id": payment.order_id,
                                  "amount_paise": integer(item, "amount"),
                                  "refunded_at": event_time(integer(item, "created_at"))},
                          raw=safe_refund(item), external_at=event_time(integer(item, "created_at")),
                          correlation_id=item.get("payment_id"), webhook_delivery_id=webhook_delivery_id)
            counts["refunds"] += 1
    settlement_days = set()
    for items in time_pages(key_id, key_secret, "settlements", period):
        for item in items:
            at = event_time(integer(item, "created_at"))
            upsert_source(db, model=Settlement, workspace_id=workspace, source_system="razorpay",
                          object_kind="settlement", external_id=item["id"],
                          event_type="razorpay_settlement_observed",
                          fields={"payment_id": None, "amount_paise": integer(item, "amount"),
                                  "prior_refund_id": None, "settled_at": at},
                          raw=safe_settlement(item), external_at=at, correlation_id=item["id"],
                          webhook_delivery_id=webhook_delivery_id)
            settlement_days.add(at.date())
            counts["settlements"] += 1
    pending = []
    for value in (record.sync_cursor or {}).get("pending_recon_days", []):
        try:
            pending.append(date.fromisoformat(value))
        except (TypeError, ValueError):
            raise RazorpayError("Stored reconciliation cursor is invalid") from None
    fresh = sorted(settlement_days, reverse=True)
    older = sorted(set(pending) - settlement_days, reverse=True)
    # Reserve capacity for both new activity and historical catch-up.
    ordered_days = list(dict.fromkeys(fresh[:11] + older[:20] + fresh[11:] + older[20:]))
    selected_days = ordered_days[:31]
    remaining_days = ordered_days[31:]
    if remaining_days:
        limitations.add(f"Reconciliation backfill has {len(remaining_days)} settlement days pending")
    for day in selected_days:
        for items in pages(key_id, key_secret, "settlements/recon/combined",
                           {"year": day.year, "month": day.month, "day": day.day}):
            for item in items:
                if item.get("currency") != "INR":
                    limitations.add("Non-INR settlement reconciliation rows were skipped")
                    continue
                safe = safe_recon(item)
                key = str(item.get("entity_id") or "") + ":" + str(item.get("settlement_id") or "")
                if not item.get("entity_id") or not item.get("settlement_id"):
                    limitations.add("A settlement reconciliation row lacked identifiers and was skipped")
                    continue
                observe(db, workspace_id=workspace, source_system="razorpay", object_kind="settlement_recon",
                        provider_object_id=key, event_type="razorpay_settlement_recon_observed", payload=safe,
                        external_at=event_time(integer(item, "settled_at")),
                        correlation_id=item.get("settlement_id"), webhook_delivery_id=webhook_delivery_id)
                counts["recon_lines"] += 1
    detect_workspace(db, workspace)
    record.last_sync_at = now
    record.sync_cursor = {"limitations": sorted(limitations),
                          "pending_recon_days": [day.isoformat() for day in remaining_days]}
    record.status = "connected"
    record.last_error = None
    db.commit()
    return {"counts": counts, "last_successful_sync_at": now.isoformat(),
            "limitations": sorted(limitations)}


@router.get("/status")
def status(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    workspace_member(workspace_id, user, db)
    return status_payload(connection(db, workspace_id, "razorpay"))


@router.post("/connect", dependencies=[Depends(same_origin)])
def connect(workspace_id: str, data: Credentials, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    require_workspace_role(workspace_id, user, db, ("owner",))
    cipher()
    if not KEY_PATTERN.fullmatch(data.key_id):
        raise HTTPException(400, "Enter a valid Razorpay key ID")
    account_available(db, workspace_id, "razorpay", data.key_id)
    record = connection(db, workspace_id, "razorpay")
    if record and record.external_account_id != data.key_id:
        raise HTTPException(409, "Disconnect the current Razorpay account before connecting another")
    try:
        request_get(data.key_id, data.key_secret, "payments", {"count": 1})
    except RazorpayError as exc:
        raise HTTPException(502, "Razorpay authentication failed") from exc
    if record is None:
        record = ProviderConnection(workspace_id=workspace_id, provider="razorpay",
                                    external_account_id=data.key_id)
        db.add(record)
    record.access_ciphertext = encrypt(data.key_id)
    record.refresh_ciphertext = encrypt(data.key_secret)
    record.status = "connected"
    record.last_error = None
    db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id,
                       action="razorpay_connected", target_id=data.key_id, detail={}))
    db.commit()
    return status_payload(record)


@router.post("/webhook-secret", dependencies=[Depends(same_origin)])
def configure_webhook(workspace_id: str, data: WebhookSecret, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    require_workspace_role(workspace_id, user, db, ("owner",))
    record = connection(db, workspace_id, "razorpay")
    if not record or not record.access_ciphertext:
        raise HTTPException(409, "Connect Razorpay before configuring a webhook")
    record.webhook_previous_secret_ciphertext = record.webhook_secret_ciphertext
    record.webhook_secret_rotated_at = (datetime.now(timezone.utc)
                                      if record.webhook_secret_ciphertext else None)
    record.webhook_secret_ciphertext = encrypt(data.secret)
    db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id,
                       action="razorpay_webhook_configured", target_id=record.id, detail={}))
    db.commit()
    return status_payload(record)


@webhook_router.post("/webhooks/{connection_id}")
async def webhook(connection_id: str, request: Request, background: BackgroundTasks,
                  db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        raise HTTPException(413, "Webhook payload is too large")
    record = db.get(ProviderConnection, connection_id)
    if (record is None or record.provider != "razorpay" or not record.access_ciphertext or
            not record.webhook_secret_ciphertext):
        raise HTTPException(404, "Webhook is not configured")
    signature = request.headers.get("x-razorpay-signature", "")
    secrets = [record.webhook_secret_ciphertext]
    rotated = record.webhook_secret_rotated_at
    if rotated and rotated.tzinfo is None:
        rotated = rotated.replace(tzinfo=timezone.utc)
    if (record.webhook_previous_secret_ciphertext and rotated and
            datetime.now(timezone.utc) - rotated <= timedelta(days=7)):
        secrets.append(record.webhook_previous_secret_ciphertext)
    if not any(hmac.compare_digest(hmac.new(decrypt(value).encode(), raw, hashlib.sha256).hexdigest(),
                                   signature) for value in secrets):
        raise HTTPException(401, "Invalid Razorpay webhook signature")
    event_id = request.headers.get("x-razorpay-event-id", "")
    if not 1 <= len(event_id) <= 160:
        raise HTTPException(400, "Missing Razorpay event ID")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, "Invalid Razorpay webhook JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("event"), str):
        raise HTTPException(400, "Invalid Razorpay webhook event")
    topic = body["event"]
    created_at = body.get("created_at")
    if (len(topic) > 100 or isinstance(created_at, bool) or not isinstance(created_at, int) or
            not 946684800 <= created_at <= int(datetime.now(timezone.utc).timestamp()) + 3600):
        raise HTTPException(400, "Invalid Razorpay webhook metadata")
    entity_kind = topic.split(".", 1)[0]
    payload = body.get("payload")
    entity_payload = payload.get(entity_kind, {}) if isinstance(payload, dict) else {}
    entity = entity_payload.get("entity", {}) if isinstance(entity_payload, dict) else {}
    source_at = entity.get("created_at") if isinstance(entity, dict) else None
    if (isinstance(source_at, bool) or not isinstance(source_at, int) or
            not 946684800 <= source_at <= created_at + 3600):
        source_at = created_at
    digest = hashlib.sha256(raw).hexdigest()
    if db.scalar(select(WebhookDelivery).where(WebhookDelivery.provider == "razorpay",
                                               WebhookDelivery.delivery_id == digest)):
        return {"accepted": True, "duplicate": True}
    delivery = WebhookDelivery(workspace_id=record.workspace_id, connection_id=record.id,
                               provider="razorpay", delivery_id=digest, event_id=event_id,
                               topic=topic, payload={"created_at": created_at,
                                                     "source_created_at": source_at},
                               status="pending" if topic in WEBHOOK_TOPICS else "ignored")
    db.add(delivery)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"accepted": True, "duplicate": True}
    if delivery.status == "pending":
        background.add_task(drain_razorpay_deliveries)
    return {"accepted": True, "duplicate": False}


def drain_razorpay_deliveries() -> None:
    """Fetch source records for verified receipts and retry transient provider failures."""
    with session_factory()() as db:
        now = datetime.now(timezone.utc)
        pending = db.scalars(select(WebhookDelivery).where(
            WebhookDelivery.provider == "razorpay",
            WebhookDelivery.status.in_(["pending", "processing"]),
            (WebhookDelivery.next_attempt_at.is_(None) | (WebhookDelivery.next_attempt_at <= now)))
            .order_by(WebhookDelivery.received_at).limit(10).with_for_update(skip_locked=True)).all()
        for delivery in pending:
            delivery.status = "processing"
            delivery.next_attempt_at = now + timedelta(minutes=5)
            db.commit()
            record = db.get(ProviderConnection, delivery.connection_id) if delivery.connection_id else None
            if record is None or not record.access_ciphertext:
                delivery.status = "ignored"
                db.commit()
                continue
            try:
                record.status = "syncing"
                db.commit()
                floor = max(946684800, int(delivery.payload["source_created_at"]) - 86400)
                sync_provider(db, record, from_time_floor=floor, webhook_delivery_id=delivery.id)
                delivery.status = "processed"
                delivery.error = None
                delivery.next_attempt_at = None
                db.commit()
            except Exception as exc:
                db.rollback()
                delivery = db.get(WebhookDelivery, delivery.id)
                delivery.attempt_count += 1
                delivery.status = "pending"
                delivery.next_attempt_at = now + timedelta(seconds=min(3600, 30 * 2 ** min(delivery.attempt_count, 10)))
                delivery.error = "Provider synchronization failed"
                record = db.get(ProviderConnection, delivery.connection_id)
                if record:
                    mark_error(record, exc)
                db.commit()


@router.post("/sync", dependencies=[Depends(same_origin)])
def sync(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_workspace_role(workspace_id, user, db)
    record = connection(db, workspace_id, "razorpay")
    if not record or not record.access_ciphertext:
        raise HTTPException(409, "Connect Razorpay before syncing")
    try:
        result = sync_provider(db, record)
        db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id,
                           action="razorpay_synced", target_id=record.id, detail=result["counts"]))
        db.commit()
        return result
    except (RazorpayError, ValueError, KeyError) as exc:
        db.rollback()
        record = connection(db, workspace_id, "razorpay")
        record.status = "error"
        record.last_error = str(exc)[:220] if isinstance(exc, RazorpayError) else "Provider synchronization failed"
        db.commit()
        raise HTTPException(502, "Razorpay sync failed; see connection status") from exc


@router.post("/disconnect", dependencies=[Depends(same_origin)])
def disconnect(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_workspace_role(workspace_id, user, db, ("owner",))
    record = connection(db, workspace_id, "razorpay")
    if record:
        record.access_ciphertext = None
        record.refresh_ciphertext = None
        record.webhook_secret_ciphertext = None
        record.webhook_previous_secret_ciphertext = None
        record.webhook_secret_rotated_at = None
        record.status = "not_connected"
        record.last_error = None
        db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id,
                           action="razorpay_disconnected", target_id=record.id, detail={}))
        db.commit()
    return status_payload(record)
