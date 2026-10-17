"""Provider contracts use signed fixtures and never contact Shopify."""
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import store
from app.live_models import Observation, Order, Product, ProviderConnection, WebhookDelivery, session_factory
from app.main import app
from app import shopify_api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "live.db").as_posix())
    monkeypatch.setenv("SESSION_SECRET", "test-secret-value-with-more-than-32-characters")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "test-client")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("SHOPIFY_REDIRECT_URI", "http://localhost:8000/live/shopify/callback")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "demo.db")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.upgrade(config, "head")
    with TestClient(app, headers={"Origin": "http://localhost:3000"}, follow_redirects=False) as browser:
        response = browser.post("/live/register", json={"email": "owner@example.com",
            "password": "a-long-unique-test-password", "workspace_name": "Test Merchant"})
        assert response.status_code == 200, response.text
        yield browser, response.json()["workspaces"][0]["id"]


def signed_query(params):
    message = "&".join(f"{key}={value}" for key, value in sorted(params.items()))
    params["hmac"] = hmac.new(b"test-client-secret", message.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


def test_oauth_state_health_credentials_and_disconnect(client, monkeypatch):
    browser, workspace = client
    path = f"/live/workspaces/{workspace}/shopify"
    assert browser.get(path + "/status").json()["status"] == "not_connected"
    response = browser.post(path + "/authorize", json={"shop": "my-test.myshopify.com"})
    assert response.status_code == 200, response.text
    nonce = parse_qs(urlparse(response.json()["authorization_url"]).query)["state"][0]
    monkeypatch.setattr(shopify_api, "exchange_code", lambda *_: {
        "access_token": "private-access", "refresh_token": "private-refresh",
        "expires_in": 3600, "refresh_token_expires_in": 7200,
        "scope": "read_products,read_inventory,read_locations,read_orders,read_returns"})
    monkeypatch.setattr(shopify_api, "shop_health", lambda *_: {"id": "gid://shopify/Shop/1", "currencyCode": "INR"})
    monkeypatch.setattr(shopify_api, "sync_connected", lambda *_: None)
    params = {"shop": "my-test.myshopify.com", "code": "one-use-code", "state": nonce,
              "timestamp": str(int(time.time()))}
    unsigned = browser.get("/live/shopify/callback?" + urlencode(params))
    assert unsigned.status_code == 400
    callback_url = "/live/shopify/callback?" + signed_query(params)
    callback = browser.get(callback_url)
    assert callback.status_code == 303, callback.text
    assert browser.get(callback_url).status_code == 400
    status = browser.get(path + "/status").json()
    assert status["status"] == "connected" and status["account"] == "my-test.myshopify.com"
    assert "private-access" not in str(status)
    with session_factory()() as db:
        record = db.scalar(select(ProviderConnection).where(ProviderConnection.workspace_id == workspace))
        assert record.access_ciphertext != "private-access"
    disconnected = browser.post(path + "/disconnect").json()
    assert disconnected["status"] == "not_connected"
    with session_factory()() as db:
        record = db.scalar(select(ProviderConnection).where(ProviderConnection.workspace_id == workspace))
        assert record.access_ciphertext is None and record.refresh_ciphertext is None


def test_signed_webhook_deduplication_and_uninstall(client, monkeypatch):
    browser, workspace = client
    with session_factory()() as db:
        db.add(ProviderConnection(workspace_id=workspace, provider="shopify",
                                  external_account_id="my-test.myshopify.com", status="connected",
                                  access_ciphertext="encrypted-placeholder"))
        db.commit()
    monkeypatch.setattr(shopify_api, "drain_deliveries", lambda: None)
    raw = b'{"id": 17}'
    signature = base64.b64encode(hmac.new(b"test-client-secret", raw, hashlib.sha256).digest()).decode()
    headers = {"x-shopify-shop-domain": "my-test.myshopify.com", "x-shopify-topic": "orders/create",
               "x-shopify-webhook-id": "delivery-1", "x-shopify-hmac-sha256": signature,
               "content-type": "application/json"}
    endpoint = "/live/shopify/webhooks"
    assert browser.post(endpoint, content=raw, headers={**headers, "x-shopify-hmac-sha256": "bad"}).status_code == 401
    assert browser.post(endpoint, content=raw, headers=headers).json() == {"accepted": True, "duplicate": False}
    assert browser.post(endpoint, content=raw, headers=headers).json() == {"accepted": True, "duplicate": True}
    with session_factory()() as db:
        assert len(db.scalars(select(WebhookDelivery)).all()) == 1
    uninstall = {**headers, "x-shopify-topic": "app/uninstalled", "x-shopify-webhook-id": "delivery-2"}
    assert browser.post(endpoint, content=raw, headers=uninstall).status_code == 200
    assert browser.get(f"/live/workspaces/{workspace}/shopify/status").json()["status"] == "not_connected"
    redaction = {**headers, "x-shopify-topic": "shop/redact", "x-shopify-webhook-id": "delivery-3"}
    assert browser.post(endpoint, content=raw, headers=redaction).status_code == 200
    with session_factory()() as db:
        record = db.scalar(select(ProviderConnection).where(ProviderConnection.workspace_id == workspace))
        assert record.external_account_id.startswith("redacted-")
        assert all("my-test.myshopify.com" not in str(item.payload)
                   for item in db.scalars(select(WebhookDelivery)).all())


def test_mocked_graphql_sync_observations_and_incident(client, monkeypatch):
    browser, workspace = client
    from app import shopify_sync
    from app.live_providers import encrypt
    with session_factory()() as db:
        db.add(ProviderConnection(workspace_id=workspace, provider="shopify",
                                  external_account_id="my-test.myshopify.com", status="connected",
                                  access_ciphertext=encrypt("private-access")))
        db.commit()
    monkeypatch.setattr(shopify_sync, "shop_health", lambda *_: {"id": "shop-1", "currencyCode": "INR"})
    variant = {"id": "variant-1", "sku": "SKU-1", "title": "Default Title", "price": "100.00",
               "updatedAt": "2026-10-15T00:00:00Z", "product": {"id": "product-1", "title": "Bag"},
               "inventoryItem": {"id": "item-1", "inventoryLevels": {"nodes": [
                   {"id": "level-1", "location": {"id": "location-1", "name": "Main"},
                    "quantities": [{"name": "available", "quantity": 3},
                                   {"name": "committed", "quantity": 1}]}],
                   "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
    order = {"id": "order-1", "name": "#1001", "createdAt": "2026-10-15T00:00:00Z",
             "updatedAt": "2026-10-15T00:01:00Z", "displayFulfillmentStatus": "UNFULFILLED",
             "totalPriceSet": {"shopMoney": {"amount": "100.00", "currencyCode": "INR"}},
             "lineItems": {"nodes": [{"id": "line-1", "sku": "SKU-1", "quantity": 1,
                "originalUnitPriceSet": {"shopMoney": {"amount": "100.00", "currencyCode": "INR"}}}],
                "pageInfo": {"hasNextPage": False, "endCursor": None}},
             "refunds": [], "returns": {"nodes": [],
                                      "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    def fake_graphql(_db, _conn, query, _variables):
        field = "productVariants" if "productVariants(" in query else "orders"
        return {field: {"nodes": [variant if field == "productVariants" else order],
                        "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    monkeypatch.setattr(shopify_sync, "graphql", fake_graphql)
    path = f"/live/workspaces/{workspace}/shopify/sync"
    first = browser.post(path)
    assert first.status_code == 200, first.text
    assert first.json()["counts"] == {"products": 1, "inventory": 1, "orders": 1}
    assert browser.get(f"/live/workspaces/{workspace}/dashboard").json()["metrics"]["order_count"] == 1
    with session_factory()() as db:
        before = len(db.scalars(select(Observation)).all())
        assert len(db.scalars(select(Product)).all()) == 1
        assert len(db.scalars(select(Order)).all()) == 1
    assert browser.post(path).status_code == 200
    with session_factory()() as db:
        assert len(db.scalars(select(Observation)).all()) == before
    snapshot = browser.post(f"/live/workspaces/{workspace}/snapshots")
    assert snapshot.status_code == 200, snapshot.text
    raw = b'{"shop_id": 1}'
    signature = base64.b64encode(hmac.new(b"test-client-secret", raw, hashlib.sha256).digest()).decode()
    redaction = browser.post("/live/shopify/webhooks", content=raw, headers={
        "x-shopify-shop-domain": "my-test.myshopify.com", "x-shopify-topic": "shop/redact",
        "x-shopify-webhook-id": "redact-after-sync", "x-shopify-hmac-sha256": signature,
        "content-type": "application/json"})
    assert redaction.status_code == 200, redaction.text
    with session_factory()() as db:
        assert db.scalars(select(Product)).all() == []
        assert db.scalars(select(Order)).all() == []
        assert db.scalars(select(Observation)).all() == []
    assert browser.get(f"/live/workspaces/{workspace}/snapshots").json() == []


def test_razorpay_read_only_sync_recon_and_bank_link(client, monkeypatch):
    browser, workspace = client
    from app import razorpay_api
    from datetime import datetime, timezone
    stamp = int(datetime.now(timezone.utc).timestamp()) - 60
    payment = {"id": "pay_abc", "order_id": None, "amount": 100000, "currency": "INR",
               "status": "captured", "captured": True, "fee": 1000, "tax": 200, "created_at": stamp}
    refund = {"id": "rfnd_abc", "payment_id": "pay_abc", "amount": 10000,
              "currency": "INR", "status": "processed", "created_at": stamp}
    settlement = {"id": "setl_abc", "amount": 88800, "status": "processed",
                  "fees": 0, "tax": 0, "utr": "bank-utr-1", "created_at": stamp}
    recon = [{"entity_id": "pay_abc", "settlement_id": "setl_abc", "payment_id": "pay_abc",
              "type": "payment", "credit": 98800, "debit": 0, "amount": 100000,
              "currency": "INR", "fee": 1000, "tax": 200, "created_at": stamp, "settled_at": stamp},
             {"entity_id": "rfnd_abc", "settlement_id": "setl_abc", "payment_id": "pay_abc",
              "type": "refund", "credit": 0, "debit": 10000, "amount": 10000,
              "currency": "INR", "fee": 0, "tax": 0, "created_at": stamp, "settled_at": stamp}]
    calls = []
    def fake_get(key_id, key_secret, path, params):
        calls.append((path, key_id))
        assert key_id == "rzp_test_12345678" and key_secret == "secret-secret-secret"
        rows = {"payments": [payment], "refunds": [refund], "settlements": [settlement],
                "settlements/recon/combined": recon}[path]
        return {"entity": "collection", "items": rows, "count": len(rows)}
    monkeypatch.setattr(razorpay_api, "request_get", fake_get)
    path = f"/live/workspaces/{workspace}/razorpay"
    connected = browser.post(path + "/connect", json={"key_id": "rzp_test_12345678",
                                                    "key_secret": "secret-secret-secret"})
    assert connected.status_code == 200, connected.text
    assert connected.json()["status"] == "connected"
    synced = browser.post(path + "/sync")
    assert synced.status_code == 200, synced.text
    assert synced.json()["counts"] == {"payments": 1, "refunds": 1, "settlements": 1, "recon_lines": 2}
    statement = "external_id,settlement_external_id,amount,posted_at\nbank-1,setl_abc,888.00,2026-10-15T12:00:00Z\n"
    imported = browser.post(f"/live/workspaces/{workspace}/imports", data={"kind": "bank_transactions"},
                            files={"file": ("bank.csv", statement.encode(), "text/csv")})
    assert imported.status_code == 200 and imported.json()["status"] == "complete", imported.text
    trail = browser.get(f"/live/workspaces/{workspace}/money/razorpay-settlements").json()
    assert trail[0]["calculated_paise"] == 88800 and trail[0]["status"] == "matched"
    assert len(trail[0]["lines"]) == 2
    extra = "external_id,settlement_external_id,amount,posted_at\nbank-2,setl_abc,1.00,2026-10-15T12:01:00Z\n"
    imported_extra = browser.post(f"/live/workspaces/{workspace}/imports", data={"kind": "bank_transactions"},
                                  files={"file": ("bank-extra.csv", extra.encode(), "text/csv")})
    assert imported_extra.status_code == 200 and imported_extra.json()["status"] == "complete"
    assert browser.get(f"/live/workspaces/{workspace}/money/razorpay-settlements").json()[0]["status"] == "bank_difference"
    assert any("Bank credits linked to Razorpay settlement" in item["summary"]
               for item in browser.get(f"/live/workspaces/{workspace}/incidents").json())
    assert all(call[0] in {"payments", "refunds", "settlements", "settlements/recon/combined"} for call in calls)
    assert browser.post(path + "/disconnect").json()["status"] == "not_connected"


def test_shopify_nested_connections_page_to_completion(monkeypatch):
    from app import shopify_sync

    level = lambda index: {"id": f"level-{index}"}
    line = lambda index: {"id": f"line-{index}"}
    return_line = lambda index: {"id": f"return-line-{index}"}
    returned = lambda index: {"id": f"return-{index}", "status": "OPEN",
                              "returnLineItems": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
    variant = {"id": "variant-1", "inventoryItem": {"id": "item-1", "inventoryLevels": {
        "nodes": [level(i) for i in range(25)],
        "pageInfo": {"hasNextPage": True, "endCursor": "level-next"}}}}
    order = {"id": "order-1", "lineItems": {
        "nodes": [line(i) for i in range(100)],
        "pageInfo": {"hasNextPage": True, "endCursor": "line-next"}},
        "returns": {"nodes": [returned(i) for i in range(25)],
                    "pageInfo": {"hasNextPage": True, "endCursor": "return-next"}}}
    order["returns"]["nodes"][0]["returnLineItems"] = {
        "nodes": [return_line(i) for i in range(100)],
        "pageInfo": {"hasNextPage": True, "endCursor": "return-line-next"}}
    calls = []

    def fake_graphql(_db, _connection, query, variables):
        calls.append((query.split("(")[0], variables))
        if "SyncVariantLevels" in query:
            return {"productVariant": {"id": "variant-1", "inventoryItem": {
                "inventoryLevels": {"nodes": [level(25)],
                                    "pageInfo": {"hasNextPage": False}}}}}
        if "SyncOrderLines" in query:
            return {"order": {"id": "order-1", "lineItems": {
                "nodes": [line(100)], "pageInfo": {"hasNextPage": False}}}}
        if "SyncOrderReturns" in query:
            return {"order": {"id": "order-1", "returns": {
                "nodes": [returned(25)], "pageInfo": {"hasNextPage": False}}}}
        if "SyncReturnLines" in query:
            return {"return": {"id": "return-0", "returnLineItems": {
                "nodes": [return_line(100)], "pageInfo": {"hasNextPage": False}}}}
        raise AssertionError("Unexpected query")

    monkeypatch.setattr(shopify_sync, "graphql", fake_graphql)
    full_variant = shopify_sync.hydrate_variant(None, None, variant)
    full_order = shopify_sync.hydrate_order(None, None, order)
    assert len(full_variant["inventoryItem"]["inventoryLevels"]["nodes"]) == 26
    assert len(full_order["lineItems"]["nodes"]) == 101
    assert len(full_order["returns"]["nodes"]) == 26
    assert len(full_order["returns"]["nodes"][0]["returnLineItems"]["nodes"]) == 101
    assert len(calls) == 4


def test_shopify_nested_pagination_rejects_repeated_cursor(monkeypatch):
    from app import shopify_sync
    from app.shopify_client import ShopifyError

    monkeypatch.setattr(shopify_sync, "graphql", lambda *_args: {"order": {
        "id": "order-1", "lineItems": {"nodes": [{"id": "line-2"}],
                                       "pageInfo": {"hasNextPage": True, "endCursor": "same"}}}})
    with pytest.raises(ShopifyError, match="page cursor"):
        shopify_sync.nested_nodes(None, None, {"lineItems": {
            "nodes": [{"id": "line-1"}],
            "pageInfo": {"hasNextPage": True, "endCursor": "same"}}},
            query=shopify_sync.ORDER_LINES_QUERY, owner_field="order",
            owner_id="order-1", path=("lineItems",))


def test_shopify_top_level_pagination_rejects_repeated_cursor(monkeypatch):
    from app import shopify_sync
    from app.shopify_client import ShopifyError

    calls = []

    def fake_graphql(_db, _connection, _query, variables):
        calls.append(variables["after"])
        return {"orders": {"nodes": [{"id": "order-1"}],
                           "pageInfo": {"hasNextPage": True, "endCursor": "same"}}}

    monkeypatch.setattr(shopify_sync, "graphql", fake_graphql)
    with pytest.raises(ShopifyError, match="page cursor"):
        list(shopify_sync.page_nodes(None, None, shopify_sync.ORDERS_QUERY, "orders"))
    assert calls == [None, "same"]


def test_shopify_older_order_scope_is_opt_in(monkeypatch):
    from app.shopify_client import requested_scopes
    monkeypatch.delenv("SHOPIFY_REQUEST_ALL_ORDERS", raising=False)
    assert "read_all_orders" not in requested_scopes()
    monkeypatch.setenv("SHOPIFY_REQUEST_ALL_ORDERS", "true")
    assert "read_all_orders" in requested_scopes()


def test_due_provider_sync_claims_once_and_retries_after_interval(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import provider_schedule

    _browser, workspace = client
    with session_factory()() as db:
        db.add(ProviderConnection(workspace_id=workspace, provider="razorpay",
                                  external_account_id="rzp_test_12345678", status="connected",
                                  access_ciphertext="encrypted-key", refresh_ciphertext="encrypted-secret"))
        db.commit()

    def fail(_db, _record):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(provider_schedule, "sync_provider", fail)
    first = provider_schedule.sync_due(provider="razorpay", interval_minutes=15)
    assert first == [{"provider": "razorpay", "workspace_id": workspace, "status": "error"}]
    assert provider_schedule.sync_due(provider="razorpay", interval_minutes=15) == []

    with session_factory()() as db:
        record = db.scalar(select(ProviderConnection).where(ProviderConnection.workspace_id == workspace))
        record.updated_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.commit()

    def succeed(db, record):
        record.status = "connected"
        record.last_sync_at = datetime.now(timezone.utc)
        db.commit()
        return {"counts": {"payments": 2}}

    monkeypatch.setattr(provider_schedule, "sync_provider", succeed)
    second = provider_schedule.sync_due(provider="razorpay", interval_minutes=15)
    assert second == [{"provider": "razorpay", "workspace_id": workspace,
                       "status": "connected", "counts": {"payments": 2}}]
    assert provider_schedule.sync_due(provider="razorpay", interval_minutes=15) == []


def test_razorpay_reconciliation_backlog_advances_across_syncs(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import razorpay_api

    _browser, workspace = client
    with session_factory()() as db:
        record = ProviderConnection(workspace_id=workspace, provider="razorpay",
                                    external_account_id="rzp_test_12345678", status="connected",
                                    access_ciphertext="key", refresh_ciphertext="secret")
        db.add(record)
        db.commit()
        record_id = record.id

    monkeypatch.setattr(razorpay_api, "decrypt", lambda value: value)
    now = datetime.now(timezone.utc)
    settlements = [{"id": f"setl_{index}", "amount": 100, "status": "processed",
                    "created_at": int((now - timedelta(days=index + 1)).timestamp())}
                   for index in range(33)]
    state = {"first": True, "recon_days": []}

    def fake_get(_key, _secret, path, params):
        if path == "settlements/recon/combined":
            state["recon_days"].append((params["year"], params["month"], params["day"]))
            items = []
        elif path == "settlements" and state["first"]:
            items = settlements
            state["first"] = False
        else:
            items = []
        return {"entity": "collection", "items": items, "count": len(items)}

    monkeypatch.setattr(razorpay_api, "request_get", fake_get)
    with session_factory()() as db:
        record = db.get(ProviderConnection, record_id)
        razorpay_api.sync_provider(db, record)
        assert len(record.sync_cursor["pending_recon_days"]) == 2
        assert len(state["recon_days"]) == 31
        razorpay_api.sync_provider(db, record)
        assert record.sync_cursor["pending_recon_days"] == []
        assert len(set(state["recon_days"])) == 33


def test_razorpay_dense_history_splits_without_duplicate_seconds(monkeypatch):
    from app import razorpay_api

    requested = []

    def fake_pages(_key, _secret, _path, params):
        start, end = params["from"], params["to"]
        requested.append((start, end))
        if end - start > 2:
            raise razorpay_api.RazorpayError(
                "Razorpay payments exceeded the configured 10,000-record sync limit")
        yield [{"second": second} for second in range(start, end + 1)]

    monkeypatch.setattr(razorpay_api, "pages", fake_pages)
    items = [item["second"] for page in razorpay_api.time_pages("key", "secret", "payments",
                                                                  {"from": 0, "to": 9}) for item in page]
    assert items == list(range(10))
    assert (0, 9) in requested and len(requested) > 1


def test_razorpay_signed_webhook_deduplicates_and_triggers_read_sync(client, monkeypatch):
    from app import razorpay_api

    browser, workspace = client
    monkeypatch.setattr(razorpay_api, "request_get", lambda *_args: {
        "entity": "collection", "items": [], "count": 0})
    base = f"/live/workspaces/{workspace}/razorpay"
    connected = browser.post(base + "/connect", json={
        "key_id": "rzp_test_12345678", "key_secret": "secret-secret-secret"})
    assert connected.status_code == 200, connected.text
    secret = "fixture-webhook-secret-123"
    configured = browser.post(base + "/webhook-secret", json={"secret": secret})
    assert configured.status_code == 200, configured.text
    path = configured.json()["webhook_path"]
    assert configured.json()["webhook_configured"] is True
    event_at = int(time.time())
    source_at = event_at - 2 * 86400
    body = json.dumps({"entity": "event", "event": "payment.captured",
                       "created_at": event_at, "payload": {"payment": {"entity": {
                           "id": "pay_fixture", "created_at": source_at,
                           "email": "private@example.com"}}}}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_fixture"}
    assert browser.post(path, content=body, headers={**headers, "X-Razorpay-Signature": "bad"}).status_code == 401

    actual_drain = razorpay_api.drain_razorpay_deliveries
    monkeypatch.setattr(razorpay_api, "drain_razorpay_deliveries", lambda: None)
    accepted = browser.post(path, content=body, headers=headers)
    assert accepted.status_code == 200 and accepted.json() == {"accepted": True, "duplicate": False}
    duplicate = browser.post(path, content=body, headers=headers)
    assert duplicate.json() == {"accepted": True, "duplicate": True}
    with session_factory()() as db:
        delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.provider == "razorpay"))
        assert delivery.status == "pending"
        assert "private@example.com" not in json.dumps(delivery.payload)
        delivery_id = delivery.id

    called = []

    def fake_sync(db, record, *, from_time_floor, webhook_delivery_id):
        called.append((record.id, from_time_floor, webhook_delivery_id))
        if len(called) == 1:
            raise RuntimeError("transient provider outage")
        record.status = "connected"
        db.commit()
        return {"counts": {}}

    monkeypatch.setattr(razorpay_api, "sync_provider", fake_sync)
    actual_drain()
    with session_factory()() as db:
        delivery = db.get(WebhookDelivery, delivery_id)
        assert delivery.status == "pending" and delivery.attempt_count == 1
        delivery.next_attempt_at = None
        db.commit()
    actual_drain()
    assert len(called) == 2 and all(item[1] == source_at - 86400 for item in called)
    with session_factory()() as db:
        assert db.get(WebhookDelivery, delivery_id).status == "processed"
    assert browser.post(base + "/webhook-secret", json={"secret": "rotated-secret-456789"}).status_code == 200
    later_body = body.replace(b"pay_fixture", b"pay_later")
    old_signature = hmac.new(secret.encode(), later_body, hashlib.sha256).hexdigest()
    rotated_retry = browser.post(path, content=later_body, headers={
        "X-Razorpay-Signature": old_signature, "X-Razorpay-Event-Id": "evt_old_secret_retry"})
    assert rotated_retry.status_code == 200 and rotated_retry.json()["accepted"] is True
