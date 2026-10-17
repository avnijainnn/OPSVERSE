"""Server-only credential vault and provider connection status."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .live_models import ProviderConnection


def cipher() -> Fernet:
    key = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(503, "Provider credential encryption is not configured") from exc


def encrypt(secret: str) -> str:
    return cipher().encrypt(secret.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(503, "Provider credentials cannot be decrypted") from exc


def connection(db: Session, workspace_id: str, provider: str) -> ProviderConnection | None:
    return db.scalar(select(ProviderConnection).where(
        ProviderConnection.workspace_id == workspace_id,
        ProviderConnection.provider == provider))


def account_available(db: Session, workspace_id: str, provider: str, account_id: str) -> None:
    owner = db.scalar(select(ProviderConnection).where(
        ProviderConnection.provider == provider,
        ProviderConnection.external_account_id == account_id))
    if owner and owner.workspace_id != workspace_id:
        raise HTTPException(409, "Provider account is already assigned to another workspace")


def public_status(record: ProviderConnection | None, *, configured: bool) -> dict:
    if record is None:
        return {"status": "not_connected", "configured": configured, "account": None,
                "last_successful_sync_at": None, "last_error": None, "can_sync": False}
    return {"status": record.status, "configured": configured,
            "account": record.external_account_id,
            "can_sync": bool(record.access_ciphertext),
            "last_successful_sync_at": record.last_sync_at.isoformat() if record.last_sync_at else None,
            "last_error": record.last_error}


def mark_error(record: ProviderConnection, exc: Exception) -> None:
    record.status = "error"
    # Provider response bodies can contain sensitive data. Expose only controlled messages.
    from .shopify_client import ShopifyError
    record.last_error = str(exc)[:220] if isinstance(exc, ShopifyError) else "Provider operation failed"
    record.updated_at = datetime.now(timezone.utc)
