"""Validated, transactional CSV ingestion for real workspace records."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .live_auth import current_user, get_db, same_origin, workspace_member, require_workspace_role
from .live_models import (AuditRecord, BankTransaction, ImportBatch, InventorySnapshot, Order,
                          OrderItem, Payment, Product, Refund, Return, Settlement, User)
from .live_observations import observe

router = APIRouter(prefix="/live/workspaces/{workspace_id}/imports")
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000

SPECS: dict[str, tuple[type, dict[str, str]]] = {
    "products": (Product, {"external_id": "text", "sku": "text", "name": "text", "price": "money"}),
    "inventory": (InventorySnapshot, {"external_id": "text", "sku": "text", "location": "text", "sellable": "count", "reserved": "count", "quarantined": "count", "observed_at": "date"}),
    "orders": (Order, {"external_id": "text", "customer_ref": "optional", "status": "text", "channel": "text", "total": "money", "placed_at": "date"}),
    "order_items": (OrderItem, {"external_id": "text", "order_external_id": "text", "sku": "text", "quantity": "positive", "unit_price": "money"}),
    "returns": (Return, {"external_id": "text", "order_external_id": "text", "sku": "text", "quantity": "positive", "condition": "text", "status": "text"}),
    "payments": (Payment, {"external_id": "text", "order_external_id": "text", "amount": "money", "fee": "money", "fee_tax": "money", "captured_at": "date"}),
    "refunds": (Refund, {"external_id": "text", "payment_external_id": "text", "amount": "positive_money", "refunded_at": "date"}),
    "settlements": (Settlement, {"external_id": "text", "payment_external_id": "text", "amount": "money", "prior_refund_external_id": "optional", "settled_at": "date"}),
    "bank_transactions": (BankTransaction, {"external_id": "text", "settlement_external_id": "optional", "amount": "money", "posted_at": "date"}),
}


def parse_money(value: str, *, positive: bool = False, paise: bool = False) -> int:
    cleaned = re.sub(r"^(INR\s*|Rs\.?\s*|₹\s*)", "", value.strip(), flags=re.I).replace(",", "")
    try:
        amount = Decimal(cleaned)
        scaled = amount if paise else amount * 100
        if not amount.is_finite() or scaled != scaled.to_integral_value():
            raise ValueError("Currency must have at most two decimal places")
        result = int(scaled)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid INR amount") from exc
    if result < 0 or (positive and result == 0):
        raise ValueError("Amount must be positive" if positive else "Amount cannot be negative")
    return result


def parse_value(value: str, kind: str, header: str = "") -> Any:
    value = value.strip()
    if kind == "optional":
        return value or None
    if not value:
        raise ValueError("Required value is empty")
    if kind == "text":
        if len(value) > 255:
            raise ValueError("Text is too long")
        return value
    if kind in ("money", "positive_money"):
        return parse_money(value, positive=kind == "positive_money", paise=header.lower().endswith("_paise"))
    if kind in ("count", "positive"):
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError("Expected an integer") from exc
        if number < 0 or (kind == "positive" and number == 0):
            raise ValueError("Quantity is out of range")
        return number
    if kind == "date":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Use an ISO 8601 date or timestamp") from exc
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)
    raise ValueError("Unsupported field")


def suggested_mapping(headers: list[str], kind: str, supplied: dict[str, str] | None = None) -> dict[str, str]:
    fields = SPECS[kind][1]
    normalized = {header.strip().lower().replace(" ", "_"): header for header in headers}
    aliases = {"external_id": ["id", f"{kind[:-1]}_id", "source_id", "item_id", "transaction_id"],
               "total": ["total_paise", "total_amount", "order_total"],
               "amount": ["amount_paise", "paid_amount"],
               "price": ["price_paise"], "unit_price": ["unit_price_paise"],
               "order_external_id": ["order_id"], "payment_external_id": ["payment_id"],
               "settlement_external_id": ["settlement_id"], "prior_refund_external_id": ["prior_refund_id"]}
    mapping: dict[str, str] = {}
    for field in fields:
        if supplied and field in supplied:
            mapping[field] = supplied[field]
        else:
            for name in [field, *aliases.get(field, [])]:
                if name in normalized:
                    mapping[field] = normalized[name]
                    break
    unknown = [field for field, header in mapping.items() if field not in fields or header not in headers]
    if unknown:
        raise ValueError("Column mapping refers to missing headers: " + ", ".join(unknown))
    return mapping


def normalize_row(row: dict[str, str], kind: str, mapping: dict[str, str]) -> dict[str, Any]:
    output = {}
    for field, value_type in SPECS[kind][1].items():
        header = mapping.get(field, "")
        if not header and value_type != "optional":
            raise ValueError(f"Map the {field} column")
        try:
            output[field] = parse_value(row.get(header, "") or "", value_type, header)
        except ValueError as exc:
            raise ValueError(f"{field}: {exc}") from exc
    return output


async def read_csv(file: UploadFile) -> tuple[bytes, list[str], list[dict[str, str]]]:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Choose a .csv file")
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "CSV exceeds 5 MB")
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        headers = reader.fieldnames or []
        if not headers or len(headers) > 80 or len(set(headers)) != len(headers):
            raise ValueError("CSV needs unique column headers (80 maximum)")
        records = list(reader)
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise HTTPException(400, f"Invalid UTF-8 CSV: {exc}") from exc
    if not records or len(records) > MAX_ROWS:
        raise HTTPException(400, "CSV must contain 1 to 10,000 rows")
    if any(None in row for row in records):
        raise HTTPException(400, "A row has more values than headers")
    return content, headers, records


def decode_mapping(value: str) -> dict[str, str]:
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in parsed.items()):
            raise ValueError()
        return parsed
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Mapping must be a JSON object of field to CSV column") from exc


def source_fields(kind: str, normalized: dict[str, Any], workspace_id: str, db: Session) -> dict[str, Any]:
    result = {key + "_paise" if key in {"price", "total", "unit_price", "amount", "fee", "fee_tax"} else key: value
              for key, value in normalized.items() if key not in {"order_external_id", "payment_external_id", "settlement_external_id", "prior_refund_external_id"}}
    reference_types = {"order_external_id": (Order, "order_id"), "payment_external_id": (Payment, "payment_id"),
                       "settlement_external_id": (Settlement, "settlement_id"), "prior_refund_external_id": (Refund, "prior_refund_id")}
    for field, (model, target) in reference_types.items():
        if field not in normalized or normalized[field] is None:
            continue
        query = select(model).where(model.workspace_id == workspace_id,
                                    model.external_id == normalized[field])
        if field != "settlement_external_id":
            query = query.where(model.source_system == "csv")
        matches = db.scalars(query.limit(2)).all()
        if len(matches) > 1:
            raise ValueError(f"{field} is ambiguous across source systems")
        linked = matches[0] if matches else None
        if linked is None:
            raise ValueError(f"{field} does not match an imported record in this workspace")
        result[target] = linked.id
    return result


def prepare(kind: str, records: list[dict[str, str]], mapping: dict[str, str], workspace_id: str, db: Session):
    model = SPECS[kind][0]
    accepted: list[tuple[dict[str, Any], dict[str, str]]] = []
    errors: list[dict[str, Any]] = []
    duplicates = 0
    seen: dict[str, dict[str, Any]] = {}
    for number, raw in enumerate(records, 2):
        try:
            normalized = normalize_row(raw, kind, mapping)
            external_id = normalized["external_id"]
            fields = source_fields(kind, normalized, workspace_id, db)
            existing = db.scalar(select(model).where(model.workspace_id == workspace_id,
                                                    model.source_system == "csv", model.external_id == external_id))
            comparable = {k: v for k, v in fields.items() if k != "external_id"}
            if existing:
                def same(left: Any, right: Any) -> bool:
                    if isinstance(left, datetime) and isinstance(right, datetime):
                        aware = lambda value: value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
                        return aware(left) == aware(right)
                    return left == right
                if any(not same(getattr(existing, key), value) for key, value in comparable.items()):
                    raise ValueError("External ID already exists with different values")
                duplicates += 1
            elif external_id in seen:
                if seen[external_id] != comparable:
                    raise ValueError("Conflicting duplicate external ID in this file")
                duplicates += 1
            else:
                seen[external_id] = comparable
                accepted.append((fields, raw))
        except ValueError as exc:
            errors.append({"row": number, "message": str(exc)})
    return accepted, duplicates, errors


@router.post("/preview", dependencies=[Depends(same_origin)])
async def preview(workspace_id: str, kind: str = Form(...), mapping: str = Form("{}"),
                  file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_workspace_role(workspace_id, user, db)
    if kind not in SPECS:
        raise HTTPException(400, "Unsupported import type")
    _, headers, records = await read_csv(file)
    try:
        mapped = suggested_mapping(headers, kind, decode_mapping(mapping))
        accepted, duplicate_count, errors = prepare(kind, records, mapped, workspace_id, db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"kind": kind, "headers": headers, "mapping": mapped, "required_fields": list(SPECS[kind][1]),
            "rows": len(records), "valid_count": len(accepted), "duplicate_count": duplicate_count,
            "errors": errors[:100], "preview": records[:5]}


@router.post("", dependencies=[Depends(same_origin)])
async def import_file(workspace_id: str, kind: str = Form(...), mapping: str = Form("{}"),
                      file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_workspace_role(workspace_id, user, db)
    if kind not in SPECS:
        raise HTTPException(400, "Unsupported import type")
    content, headers, records = await read_csv(file)
    try:
        mapped = suggested_mapping(headers, kind, decode_mapping(mapping))
        accepted, duplicates, errors = prepare(kind, records, mapped, workspace_id, db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    batch = ImportBatch(workspace_id=workspace_id, user_id=user.id, kind=kind,
                        filename=(file.filename or "upload.csv")[:255], sha256=hashlib.sha256(content).hexdigest(),
                        status="rejected" if errors else "complete", imported_count=0 if errors else len(accepted),
                        duplicate_count=duplicates, errors=errors[:100])
    db.add(batch)
    db.flush()
    if not errors:
        model = SPECS[kind][0]
        names = {"products": "product", "inventory": "inventory", "orders": "order",
                 "order_items": "order_item", "returns": "return", "payments": "payment",
                 "refunds": "refund", "settlements": "settlement", "bank_transactions": "bank_transaction"}
        from .live_observations import normalized_state
        for fields, raw in accepted:
            record = model(workspace_id=workspace_id, import_batch_id=batch.id,
                           source_system="csv", raw=raw, **fields)
            db.add(record)
            db.flush()
            observe(db, workspace_id=workspace_id, source_system="csv",
                    object_kind=names[kind], provider_object_id=record.external_id,
                    event_type="csv_imported", payload={"row": raw, "filename": batch.filename,
                                                        "normalized": normalized_state(record)},
                    normalized_record_id=record.id, import_batch_id=batch.id,
                    idempotency_key=f"{batch.id}:{kind}:{record.external_id}")
        db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id, action="csv_import",
                           target_id=batch.id, detail={"kind": kind, "rows": len(accepted)}))
    db.flush()
    from .live_detection import detect_workspace
    detect_workspace(db, workspace_id)
    db.commit()
    return {"id": batch.id, "kind": kind, "status": batch.status, "imported_count": batch.imported_count,
            "duplicate_count": batch.duplicate_count, "errors": batch.errors}


@router.get("")
def history(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    workspace_member(workspace_id, user, db)
    batches = db.scalars(select(ImportBatch).where(ImportBatch.workspace_id == workspace_id)
                         .order_by(ImportBatch.created_at.desc()).limit(100)).all()
    return [{"id": b.id, "kind": b.kind, "filename": b.filename, "status": b.status,
             "imported_count": b.imported_count, "duplicate_count": b.duplicate_count,
             "errors": b.errors, "created_at": b.created_at.isoformat()} for b in batches]
