"""Workspace-scoped Shopify authorization, synchronization and verified deliveries."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .live_auth import current_user, get_db, same_origin, workspace_member, require_workspace_role
from .live_models import (AuditRecord, InventorySnapshot, LiveIncident, LiveInvestigation,
                          LiveScenario, LiveSnapshot, OAuthState, Observation, Order, OrderItem, Payment,
                          Product, ProviderConnection, Refund, Return, User, WebhookDelivery,
                          session_factory)
from .live_providers import account_available, cipher, connection, mark_error, public_status
from .shopify_client import (ShopifyError, app_config, authorize_url, exchange_code,
                             shop_health, store_tokens, validate_shop, verify_oauth_hmac,
                             verify_webhook)
from .shopify_sync import sync_shopify

router = APIRouter(prefix="/live")
ACTIVE_TOPICS = {"products/create", "products/update", "products/delete", "inventory_levels/update",
                 "orders/create", "orders/updated", "orders/cancelled", "refunds/create", "returns/approve",
                 "returns/process", "returns/close"}
PRIVACY_TOPICS = {"customers/data_request", "customers/redact", "shop/redact"}


class AuthorizeInput(BaseModel):
    shop: str


def configured() -> bool:
    return all(os.getenv(key) for key in ("SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET",
                                           "SHOPIFY_REDIRECT_URI", "CREDENTIAL_ENCRYPTION_KEY"))


def frontend_url(status: str) -> str:
    origin = os.getenv("FRONTEND_URL") or os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")[0]
    return origin.rstrip("/") + "/live?" + urlencode({"shopify": status})


@router.post("/workspaces/{workspace_id}/shopify/authorize", dependencies=[Depends(same_origin)])
def authorize(workspace_id: str, data: AuthorizeInput, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    require_workspace_role(workspace_id, user, db, ("owner",))
    app_config()
    cipher()
    shop = validate_shop(data.shop)
    account_available(db, workspace_id, "shopify", shop)
    nonce = secrets.token_urlsafe(32)
    db.add(OAuthState(nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
                      workspace_id=workspace_id, user_id=user.id, shop_domain=shop,
                      expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    db.commit()
    return {"authorization_url": authorize_url(shop, nonce)}


@router.get("/shopify/callback")
def callback(request: Request, background: BackgroundTasks,
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    params = request.query_params
    if any(len(params.getlist(key)) != 1 for key in ("shop", "code", "state", "hmac", "timestamp")):
        raise HTTPException(400, "Shopify callback is missing or duplicates required parameters")
    values = dict(params)
    if not verify_oauth_hmac(values):
        raise HTTPException(401, "Invalid Shopify callback signature")
    try:
        timestamp = int(values["timestamp"])
    except ValueError as exc:
        raise HTTPException(400, "Invalid Shopify callback timestamp") from exc
    if abs(datetime.now(timezone.utc).timestamp() - timestamp) > 600:
        raise HTTPException(400, "Expired Shopify callback")
    shop = validate_shop(values["shop"])
    state = db.scalar(select(OAuthState).where(OAuthState.nonce_hash ==
                       hashlib.sha256(values["state"].encode()).hexdigest()))
    now = datetime.now(timezone.utc)
    if not state or state.consumed_at or state.shop_domain != shop or state.user_id != user.id:
        raise HTTPException(400, "Invalid or used Shopify authorization state")
    expiry = state.expires_at.replace(tzinfo=timezone.utc) if state.expires_at.tzinfo is None else state.expires_at
    if expiry < now:
        raise HTTPException(400, "Shopify authorization state expired")
    workspace_member(state.workspace_id, user, db)
    account_available(db, state.workspace_id, "shopify", shop)
    state.consumed_at = now
    db.commit()  # The authorization code and state may be used only once, including on failure.
    try:
        tokens = exchange_code(shop, values["code"])
        record = connection(db, state.workspace_id, "shopify")
        if record and record.external_account_id != shop:
            raise HTTPException(409, "Disconnect the current Shopify store before connecting another")
        if record is None:
            record = ProviderConnection(workspace_id=state.workspace_id, provider="shopify",
                                        external_account_id=shop, status="pending")
            db.add(record)
        store_tokens(record, tokens)
        identity = shop_health(db, record)
        if identity.get("currencyCode") != "INR":
            raise ShopifyError("Store currency is not INR; conversion is not configured")
        record.status = "connected"
        db.add(AuditRecord(workspace_id=state.workspace_id, user_id=user.id,
                           action="shopify_connected", target_id=shop,
                           detail={"shop_id": identity["id"], "scopes": record.scopes}))
        db.commit()
        background.add_task(sync_connected, record.id)
        return RedirectResponse(frontend_url("connected"), status_code=303)
    except (ShopifyError, HTTPException) as exc:
        db.rollback()
        record = connection(db, state.workspace_id, "shopify")
        if record:
            mark_error(record, exc)
            db.commit()
        raise


def sync_connected(connection_id: str) -> None:
    with session_factory()() as db:
        record = db.get(ProviderConnection, connection_id)
        if record is None or not record.access_ciphertext:
            return
        try:
            sync_shopify(db, record)
        except Exception as exc:
            db.rollback()
            record = db.get(ProviderConnection, connection_id)
            mark_error(record, exc)
            db.commit()


@router.get("/workspaces/{workspace_id}/shopify/status")
def status(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    workspace_member(workspace_id, user, db)
    return public_status(connection(db, workspace_id, "shopify"), configured=configured())


@router.post("/workspaces/{workspace_id}/shopify/sync", dependencies=[Depends(same_origin)])
def sync(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_workspace_role(workspace_id, user, db)
    record = connection(db, workspace_id, "shopify")
    if not record or not record.access_ciphertext:
        raise HTTPException(409, "Connect Shopify before syncing")
    try:
        result = sync_shopify(db, record)
        db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id,
                           action="shopify_synced", target_id=record.id, detail=result["counts"]))
        db.commit()
        return result
    except (ShopifyError, ValueError, KeyError) as exc:
        db.rollback()
        record = connection(db, workspace_id, "shopify")
        mark_error(record, exc)
        db.commit()
        raise HTTPException(502, "Shopify sync failed; see connection status") from exc


@router.post("/workspaces/{workspace_id}/shopify/disconnect", dependencies=[Depends(same_origin)])
def disconnect(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_workspace_role(workspace_id, user, db, ("owner",))
    record = connection(db, workspace_id, "shopify")
    if record:
        record.status = "not_connected"
        record.access_ciphertext = None
        record.refresh_ciphertext = None
        record.token_expires_at = None
        record.refresh_expires_at = None
        record.last_error = None
        db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id,
                           action="shopify_disconnected", target_id=record.id, detail={}))
        db.commit()
    return public_status(record, configured=configured())


def drain_deliveries() -> None:
    """Retry durable verified deliveries. Any active data topic triggers a scoped incremental pull."""
    with session_factory()() as db:
        now = datetime.now(timezone.utc)
        pending = db.scalars(select(WebhookDelivery).where(
            WebhookDelivery.provider == "shopify",
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
                sync_shopify(db, record, webhook_delivery_id=delivery.id)
                delivery.status = "processed"
                delivery.error = None
                delivery.next_attempt_at = None
                db.commit()
            except Exception as exc:
                db.rollback()
                delivery = db.get(WebhookDelivery, delivery.id)
                delivery.attempt_count += 1
                delivery.status = "failed" if delivery.attempt_count >= 5 else "pending"
                delivery.next_attempt_at = now + timedelta(seconds=min(3600, 30 * 2 ** delivery.attempt_count))
                delivery.error = "Provider synchronization failed"
                record = db.get(ProviderConnection, delivery.connection_id)
                if record:
                    mark_error(record, exc)
                db.commit()


def redact_shop(db: Session, record: ProviderConnection) -> None:
    """Erase previously ingested Shopify material and derived copies on shop/redact."""
    workspace = record.workspace_id
    product_ids = {item.id for item in db.scalars(select(Product).where(
        Product.workspace_id == workspace, Product.source_system == "shopify"))}
    order_ids = {item.id for item in db.scalars(select(Order).where(
        Order.workspace_id == workspace, Order.source_system == "shopify"))}
    inventory_ids = {item.id for item in db.scalars(select(InventorySnapshot).where(
        InventorySnapshot.workspace_id == workspace, InventorySnapshot.source_system == "shopify"))}
    evidence_ids = product_ids | order_ids | inventory_ids
    for model in (OrderItem, Return, Refund):
        evidence_ids.update(item.id for item in db.scalars(select(model).where(
            model.workspace_id == workspace, model.source_system == "shopify")))
    for item in db.scalars(select(LiveSnapshot).where(LiveSnapshot.workspace_id == workspace)).all():
        refs = item.source_state.get("source_refs", [])
        if any(ref.get("source") == "shopify" for ref in refs):
            db.execute(delete(LiveScenario).where(LiveScenario.snapshot_id == item.id))
            db.delete(item)
    for item in db.scalars(select(LiveIncident).where(LiveIncident.workspace_id == workspace)).all():
        if any(ref.get("id") in evidence_ids for ref in item.evidence):
            db.execute(delete(LiveInvestigation).where(LiveInvestigation.incident_id == item.id))
            db.delete(item)
    for item in db.scalars(select(Payment).where(Payment.workspace_id == workspace,
                                                  Payment.order_id.in_(order_ids))).all() if order_ids else []:
        item.order_id = None
    for item in db.scalars(select(Refund).where(Refund.workspace_id == workspace,
                                                Refund.order_id.in_(order_ids))).all() if order_ids else []:
        item.order_id = None
    db.flush()
    for model in (OrderItem, Return, Refund, Order, InventorySnapshot, Product):
        db.execute(delete(model).where(model.workspace_id == workspace, model.source_system == "shopify"))
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SET LOCAL opsverse.allow_observation_redaction = 'on'"))
    db.execute(delete(Observation).where(Observation.workspace_id == workspace,
                                         Observation.source_system == "shopify"))
    for delivery in db.scalars(select(WebhookDelivery).where(
        WebhookDelivery.connection_id == record.id)).all():
        delivery.payload = {"redacted": True}
        delivery.workspace_id = None
    record.status = "not_connected"
    record.access_ciphertext = None
    record.refresh_ciphertext = None
    record.external_account_id = "redacted-" + str(uuid4())
    record.sync_cursor = {}
    record.last_error = None
    db.commit()


@router.post("/shopify/webhooks")
async def webhook(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        raise HTTPException(413, "Webhook payload too large")
    if not verify_webhook(raw, request.headers.get("x-shopify-hmac-sha256", "")):
        raise HTTPException(401, "Invalid Shopify webhook signature")
    shop = validate_shop(request.headers.get("x-shopify-shop-domain", ""))
    topic = request.headers.get("x-shopify-topic", "").lower()
    delivery_id = request.headers.get("x-shopify-webhook-id", "")
    if not delivery_id or len(delivery_id) > 160:
        raise HTTPException(400, "Missing Shopify delivery ID")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(400, "Invalid Shopify webhook JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid Shopify webhook object")
    existing = db.scalar(select(WebhookDelivery).where(WebhookDelivery.provider == "shopify",
                                                    WebhookDelivery.delivery_id == delivery_id))
    if existing:
        return {"accepted": True, "duplicate": True}
    record = db.scalar(select(ProviderConnection).where(ProviderConnection.provider == "shopify",
                                                        ProviderConnection.external_account_id == shop))
    delivery = WebhookDelivery(workspace_id=record.workspace_id if record else None,
                               connection_id=record.id if record else None, provider="shopify",
                               delivery_id=delivery_id, event_id=request.headers.get("x-shopify-event-id", "")[:160],
                               topic=topic[:100], payload={"id": str(payload.get("id", ""))[:160],
                                                           "shop": shop, "sha256": hashlib.sha256(raw).hexdigest()},
                               status="pending" if record and topic in ACTIVE_TOPICS else "ignored")
    db.add(delivery)
    if record and topic == "app/uninstalled":
        record.status = "not_connected"
        record.access_ciphertext = None
        record.refresh_ciphertext = None
        delivery.status = "processed"
    if record and topic in PRIVACY_TOPICS:
        # We do not ingest protected customer fields. Shop erasure also removes credentials.
        delivery.status = "processed"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"accepted": True, "duplicate": True}
    if record and topic == "shop/redact":
        redact_shop(db, record)
    if delivery.status == "pending":
        background.add_task(drain_deliveries)
    return {"accepted": True, "duplicate": False}
