"""Pinned read-only Shopify Admin GraphQL transport and standalone OAuth primitives."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .live_models import ProviderConnection
from .live_providers import decrypt, encrypt

API_VERSION = "2026-10"
SCOPES = ("read_products", "read_inventory", "read_locations", "read_orders", "read_returns")
SHOP_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]*\.myshopify\.com$")


class ShopifyError(RuntimeError):
    pass


def requested_scopes() -> tuple[str, ...]:
    """Older orders require merchant-approved read_all_orders access."""
    return SCOPES + (("read_all_orders",) if os.getenv("SHOPIFY_REQUEST_ALL_ORDERS") == "true" else ())


def app_config() -> tuple[str, str, str]:
    values = tuple(os.getenv(key, "") for key in
                   ("SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_REDIRECT_URI"))
    if not all(values):
        raise HTTPException(503, "Shopify app credentials and redirect URI are not configured")
    if not values[2].startswith("https://") and not values[2].startswith("http://localhost:"):
        raise HTTPException(503, "Shopify redirect URI must use HTTPS outside localhost")
    return values


def validate_shop(value: str) -> str:
    shop = value.strip().lower()
    if not SHOP_PATTERN.fullmatch(shop):
        raise HTTPException(400, "Enter a valid myshopify.com store domain")
    return shop


def authorize_url(shop: str, nonce: str) -> str:
    client_id, _, redirect_uri = app_config()
    return f"https://{validate_shop(shop)}/admin/oauth/authorize?" + urlencode({
        "client_id": client_id, "scope": ",".join(requested_scopes()),
        "redirect_uri": redirect_uri, "state": nonce})


def verify_oauth_hmac(params: dict[str, str]) -> bool:
    _, client_secret, _ = app_config()
    signature = params.get("hmac", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", signature):
        return False
    message = "&".join(f"{key}={value}" for key, value in sorted(params.items()) if key != "hmac")
    digest = hmac.new(client_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def verify_webhook(raw: bytes, signature: str) -> bool:
    _, client_secret, _ = app_config()
    calculated = base64.b64encode(hmac.new(client_secret.encode(), raw, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(calculated, signature)


def _request(url: str, *, data: dict | None = None, json_data: dict | None = None,
             token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Shopify-Access-Token"] = token
    for attempt in range(3):
        try:
            with httpx.Client(timeout=httpx.Timeout(12.0, connect=4.0), follow_redirects=False) as client:
                response = client.post(url, data=data, json=json_data, headers=headers)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                try:
                    delay = min(2.0, float(response.headers.get("Retry-After", "0.5")) or 0.5)
                except ValueError:
                    delay = 0.5
                time.sleep(max(0.1, delay))
                continue
            if response.status_code >= 400:
                raise ShopifyError(f"Shopify returned HTTP {response.status_code}")
            body = response.json()
            if not isinstance(body, dict):
                raise ShopifyError("Shopify returned an invalid response")
            return body
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == 2:
                raise ShopifyError("Shopify request failed after retries") from exc
            time.sleep(0.2 * (attempt + 1))
    raise ShopifyError("Shopify request failed")


def exchange_code(shop: str, code: str) -> dict:
    client_id, client_secret, _ = app_config()
    body = _request(f"https://{validate_shop(shop)}/admin/oauth/access_token", data={
        "client_id": client_id, "client_secret": client_secret,
        "code": code, "expiring": "1"})
    if not body.get("access_token") or not body.get("refresh_token"):
        raise ShopifyError("Shopify did not issue an expiring offline token pair")
    if not set(requested_scopes()).issubset(set(str(body.get("scope", "")).split(","))):
        raise ShopifyError("Shopify did not grant the required read scopes")
    return body


def store_tokens(connection: ProviderConnection, body: dict) -> None:
    now = datetime.now(timezone.utc)
    connection.access_ciphertext = encrypt(str(body["access_token"]))
    connection.refresh_ciphertext = encrypt(str(body["refresh_token"]))
    connection.token_expires_at = now + timedelta(seconds=int(body["expires_in"]))
    connection.refresh_expires_at = now + timedelta(seconds=int(body["refresh_token_expires_in"]))
    connection.scopes = str(body["scope"])
    connection.last_error = None


def access_token(db: Session, connection: ProviderConnection) -> str:
    if not connection.access_ciphertext:
        raise ShopifyError("Shopify connection needs reauthorization")
    now = datetime.now(timezone.utc)
    expires = connection.token_expires_at
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires and expires > now + timedelta(minutes=2):
        return decrypt(connection.access_ciphertext)
    if not connection.refresh_ciphertext:
        raise ShopifyError("Shopify refresh token is unavailable; reconnect the store")
    client_id, client_secret, _ = app_config()
    body = _request(f"https://{validate_shop(connection.external_account_id)}/admin/oauth/access_token",
                    data={"client_id": client_id, "client_secret": client_secret,
                          "grant_type": "refresh_token",
                          "refresh_token": decrypt(connection.refresh_ciphertext)})
    if not body.get("access_token") or not body.get("refresh_token"):
        raise ShopifyError("Shopify token refresh failed; reconnect the store")
    body.setdefault("scope", connection.scopes)
    store_tokens(connection, body)
    db.commit()
    return decrypt(connection.access_ciphertext)


def graphql(db: Session, connection: ProviderConnection, query: str,
            variables: dict | None = None) -> dict:
    url = f"https://{validate_shop(connection.external_account_id)}/admin/api/{API_VERSION}/graphql.json"
    response = _request(url, json_data={"query": query, "variables": variables or {}},
                        token=access_token(db, connection))
    if response.get("errors"):
        messages = [str(item.get("message", "GraphQL error"))[:180] for item in response["errors"][:3]]
        raise ShopifyError("Shopify GraphQL rejected a query: " + "; ".join(messages))
    data = response.get("data")
    if not isinstance(data, dict):
        raise ShopifyError("Shopify GraphQL response has no data")
    return data


def shop_health(db: Session, connection: ProviderConnection) -> dict:
    shop = graphql(db, connection, "query { shop { id name currencyCode } }").get("shop")
    if not shop or not shop.get("id"):
        raise ShopifyError("Shopify did not return a store identity")
    return shop
