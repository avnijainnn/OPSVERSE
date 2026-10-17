"""Runtime checks that require a real PostgreSQL database.

Set POSTGRES_TEST_URL to a disposable database. Never point it at production.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.live_ai import RelatedArgs, SourceArgs, related_records, source_record
from app.live_models import Observation, Product, ProviderConnection, WebhookDelivery, engine, session_factory
from app.live_observations import upsert_source
from app.main import app


@pytest.fixture(scope="module", autouse=True)
def postgres_runtime():
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_URL is required for PostgreSQL runtime checks")
    from sqlalchemy.engine import make_url
    if make_url(url).get_backend_name() != "postgresql":
        pytest.fail("POSTGRES_TEST_URL must use PostgreSQL")
    previous = {key: os.environ.get(key) for key in (
        "DATABASE_URL", "SESSION_SECRET", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET",
        "SHOPIFY_REDIRECT_URI")}
    os.environ["DATABASE_URL"] = url
    os.environ["SESSION_SECRET"] = "postgres-runtime-test-secret-with-32-chars"
    os.environ["SHOPIFY_CLIENT_ID"] = "postgres-runtime-shopify-test-client"
    os.environ["SHOPIFY_CLIENT_SECRET"] = "postgres-runtime-shopify-test-secret"
    os.environ["SHOPIFY_REDIRECT_URI"] = "http://localhost:8000/live/shopify/callback"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.upgrade(config, "head")
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def register() -> tuple[TestClient, str]:
    browser = TestClient(app, headers={"Origin": "http://localhost:3000"})
    response = browser.post("/live/register", json={
        "email": f"pg-{uuid4().hex}@example.test", "password": "a-unique-postgres-test-password",
        "workspace_name": "Postgres Runtime Test"})
    assert response.status_code == 200, response.text
    return browser, response.json()["workspaces"][0]["id"]


def test_postgres_observations_are_immutable_and_unique():
    browser, workspace = register()
    try:
        response = browser.post(f"/live/workspaces/{workspace}/imports", data={"kind": "products"},
                                files={"file": ("products.csv", b"external_id,sku,name,price\np1,PG-1,Postgres bag,100.00\n", "text/csv")})
        assert response.status_code == 200, response.text
        with session_factory()() as db:
            original = db.scalar(select(Observation).where(Observation.workspace_id == workspace))
            assert original is not None
            observation_id, key = original.id, original.idempotency_key
        with pytest.raises(DBAPIError, match="append-only"):
            with engine().begin() as conn:
                conn.execute(update(Observation).where(Observation.id == observation_id)
                             .values(event_type="tampered"))
        with pytest.raises(DBAPIError, match="append-only"):
            with engine().begin() as conn:
                conn.execute(delete(Observation).where(Observation.id == observation_id))
        with session_factory()() as db:
            db.add(Observation(workspace_id=workspace, source_system="csv", object_kind="product",
                               provider_object_id="p1", event_type="duplicate", idempotency_key=key,
                               payload={"test": True}))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
            assert db.get(Observation, observation_id).event_type != "tampered"
    finally:
        browser.close()


def test_postgres_shop_redaction_erases_observations():
    browser, workspace = register()
    shop = f"pg-{uuid4().hex[:12]}.myshopify.com"
    with session_factory()() as db:
        db.add(ProviderConnection(workspace_id=workspace, provider="shopify",
                                  external_account_id=shop, status="connected",
                                  access_ciphertext="test-only"))
        upsert_source(db, model=Product, workspace_id=workspace, source_system="shopify",
                      object_kind="product", external_id="gid://shopify/ProductVariant/1",
                      event_type="shopify_variant_observed",
                      fields={"sku": "PG-SKU", "name": "Test bag", "price_paise": 10000},
                      raw={"id": "gid://shopify/ProductVariant/1"}, external_at=None)
        db.commit()
    raw = b'{"shop_id":1}'
    signature = base64.b64encode(hmac.new(b"postgres-runtime-shopify-test-secret", raw,
                                          hashlib.sha256).digest()).decode()
    response = browser.post("/live/shopify/webhooks", content=raw, headers={
        "x-shopify-shop-domain": shop, "x-shopify-topic": "shop/redact",
        "x-shopify-webhook-id": f"redact-{uuid4().hex}", "x-shopify-hmac-sha256": signature,
        "content-type": "application/json"})
    assert response.status_code == 200, response.text
    with session_factory()() as db:
        assert db.scalars(select(Product).where(Product.workspace_id == workspace)).all() == []
        assert db.scalars(select(Observation).where(Observation.workspace_id == workspace)).all() == []
        record = db.scalar(select(ProviderConnection).where(ProviderConnection.workspace_id == workspace))
        assert record.status == "not_connected" and record.access_ciphertext is None
    browser.close()


def test_concurrent_signed_webhooks_are_idempotent_and_workspaces_isolated():
    first, workspace = register()
    second, other = register()
    assert first.get(f"/live/workspaces/{other}/dashboard").status_code == 404
    assert second.get(f"/live/workspaces/{workspace}/dashboard").status_code == 404
    first_cookies, second_cookies = dict(first.cookies.items()), dict(second.cookies.items())
    def read_workspace(index: int) -> int:
        own = index % 4 in (0, 2)
        first_account = index % 4 in (0, 1)
        target = (workspace if first_account else other) if own else (other if first_account else workspace)
        with TestClient(app) as client:
            client.cookies.update(first_cookies if first_account else second_cookies)
            return client.get(f"/live/workspaces/{target}/dashboard").status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(read_workspace, range(16)))
    assert statuses == [200, 404, 200, 404] * 4
    for suffix in ("incidents", "investigations", "money", "snapshots", "scenarios",
                   "observations", "audit", "integrations"):
        assert second.get(f"/live/workspaces/{workspace}/{suffix}").status_code == 404
    first.post(f"/live/workspaces/{workspace}/imports", data={"kind": "products"},
               files={"file": ("products.csv", b"external_id,sku,name,price\nscoped-1,SCOPED-1,Private bag,50.00\n", "text/csv")})
    with session_factory()() as db:
        private = db.scalar(select(Product).where(Product.workspace_id == workspace))
        assert private is not None
        with pytest.raises(ValueError, match="unavailable in this workspace"):
            source_record(db, other, SourceArgs(kind="product", id=private.id))
        assert related_records(db, other, RelatedArgs(external_id=private.external_id)) == []
    raw = b'{"id":1}'
    signature = base64.b64encode(hmac.new(b"postgres-runtime-shopify-test-secret", raw,
                                          hashlib.sha256).digest()).decode()
    delivery_id = f"concurrent-{uuid4().hex}"
    headers = {"x-shopify-shop-domain": "unknown-shop.myshopify.com",
               "x-shopify-topic": "customers/data_request", "x-shopify-webhook-id": delivery_id,
               "x-shopify-hmac-sha256": signature, "content-type": "application/json"}
    def send(_: int) -> tuple[int, dict]:
        with TestClient(app) as client:
            response = client.post("/live/shopify/webhooks", content=raw, headers=headers)
            return response.status_code, response.json()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(send, range(12)))
    assert all(status == 200 and body["accepted"] for status, body in results)
    assert sum(not body["duplicate"] for _, body in results) == 1
    with session_factory()() as db:
        assert len(db.scalars(select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)).all()) == 1
    first.close()
    second.close()


def test_postgres_rejected_batch_rolls_back_and_cutoff_survives_connection_restart():
    browser, workspace = register()
    def upload(kind: str, csv: str):
        response = browser.post(f"/live/workspaces/{workspace}/imports", data={"kind": kind},
                                files={"file": (f"{kind}.csv", csv.encode(), "text/csv")})
        assert response.status_code == 200, response.text
        return response.json()
    upload("products", "external_id,sku,name,price\np1,PG-CUT,Postgres bag,100.00\n")
    upload("inventory", "external_id,sku,location,sellable,reserved,quarantined,observed_at\n"
          "first,PG-CUT,Main,5,0,0,2026-10-15T09:00:00Z\n")
    cutoff = max(item["ingested_at"] for item in browser.get(
        f"/live/workspaces/{workspace}/observations").json())
    rejected = upload("products", "external_id,sku,name,price\n"
                      "p2,PG-ROLL,Would be committed,90.00\n"
                      "p3,PG-BAD,Missing price,\n")
    assert rejected["status"] == "rejected" and rejected["imported_count"] == 0
    assert browser.get(f"/live/workspaces/{workspace}/dashboard").json()["metrics"]["product_count"] == 1
    upload("inventory", "external_id,sku,location,sellable,reserved,quarantined,observed_at\n"
          "second,PG-CUT,Main,2,0,0,2026-10-15T09:00:00Z\n")
    snapshot = browser.post(f"/live/workspaces/{workspace}/snapshots",
                            json={"observed_cutoff": cutoff})
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["source_record_count"] == 2
    cookies = dict(browser.cookies.items())
    browser.close()
    engine().dispose()
    with TestClient(app, headers={"Origin": "http://localhost:3000"}) as restarted:
        restarted.cookies.update(cookies)
        assert restarted.get(f"/live/workspaces/{workspace}/dashboard").json()["record_counts"]["inventory_snapshots"] == 2
        assert restarted.get(f"/live/workspaces/{workspace}/snapshots").json()[0]["id"] == snapshot.json()["id"]
