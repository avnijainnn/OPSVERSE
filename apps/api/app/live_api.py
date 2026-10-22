"""Authenticated read and analysis endpoints for imported business records."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .live_ai import KINDS, SourceArgs, investigate, proposals_from_trace, source_record, structured_investigation
from .live_evidence import evidence_graph
from .live_backtest import evaluate_order_backtest
from .live_demand import VERSION as DEMAND_MODEL_VERSION, demand_model, modeled_schedule
from .live_receipt import receipt_data, render_receipt
from .live_rate_limit import check_analysis_budget
from .live_auth import current_user, get_db, same_origin, workspace_member
from .live_models import (AuditRecord, BankTransaction, ImportBatch, InventorySnapshot,
                          LiveIncident, LiveInvestigation, LiveScenario, LiveSnapshot,
                          Observation, Order, OrderItem, Payment, Product, ProviderConnection,
                          Refund, Return, Settlement, User)
from .live_twin import explain_metric_difference, find_first_branch_divergence, freeze_state, simulate_branches, utc
from .live_providers import connection, public_status
from .shopify_api import configured as shopify_configured

router = APIRouter(prefix="/live/workspaces/{workspace_id}")


def scoped(workspace_id: str, user: User, db: Session) -> None:
    workspace_member(workspace_id, user, db)


def incident_data(record: LiveIncident) -> dict:
    return {"id": record.id, "kind": record.kind, "classification": record.classification,
            "status": record.status, "summary": record.summary, "evidence": record.evidence,
            "created_at": record.created_at.isoformat()}


def incident_sources(db: Session, workspace_id: str, refs: list[dict]) -> list[dict]:
    result = []
    for ref in refs:
        try:
            result.append(source_record(db, workspace_id, SourceArgs(kind=ref["kind"], id=ref["id"])))
        except ValueError:
            result.append({"id": ref.get("id"), "kind": ref.get("kind"),
                           "status": "unavailable", "reason": "Referenced source record is unavailable"})
    return result


@router.get("/dashboard")
def dashboard(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    products = db.scalars(select(Product).where(Product.workspace_id == workspace_id)).all()
    orders = db.scalars(select(Order).where(Order.workspace_id == workspace_id)).all()
    inventory = db.scalars(select(InventorySnapshot).where(InventorySnapshot.workspace_id == workspace_id)).all()
    payments = db.scalars(select(Payment).where(Payment.workspace_id == workspace_id)).all()
    returns = db.scalars(select(Return).where(Return.workspace_id == workspace_id)).all()
    bank = db.scalars(select(BankTransaction).where(BankTransaction.workspace_id == workspace_id)).all()
    settlements = db.scalars(select(Settlement).where(Settlement.workspace_id == workspace_id)).all()
    refunds = db.scalars(select(Refund).where(Refund.workspace_id == workspace_id)).all()
    order_items = db.scalars(select(OrderItem).where(OrderItem.workspace_id == workspace_id)).all()
    incidents = db.scalars(select(LiveIncident).where(LiveIncident.workspace_id == workspace_id,
                                                  LiveIncident.status == "open")).all()
    latest: dict[tuple[str, str], InventorySnapshot] = {}
    for item in inventory:
        key = (item.sku, item.location)
        order_key = lambda row: ((row.observed_at.replace(tzinfo=timezone.utc) if row.observed_at.tzinfo is None else row.observed_at.astimezone(timezone.utc)), row.id)
        if key not in latest or order_key(item) > order_key(latest[key]):
            latest[key] = item
    imports = db.scalars(select(ImportBatch).where(ImportBatch.workspace_id == workspace_id)
                         .order_by(ImportBatch.created_at.desc()).limit(10)).all()
    return {"mode": "live_imported", "workspace_id": workspace_id,
            "analysis_models": {"incident_llm": ("credentials_present" if all(
                os.getenv(key) for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"))
                else "unconfigured"), "demand_model": DEMAND_MODEL_VERSION},
            "metrics": {"gross_orders_paise": sum(item.total_paise for item in orders),
                        "order_count": len(orders), "product_count": len(products),
                        "sellable_units": sum(item.sellable for item in latest.values()),
                        "reserved_units": sum(item.reserved for item in latest.values()),
                        "captured_paise": sum(item.amount_paise for item in payments),
                        "bank_received_paise": sum(item.amount_paise for item in bank),
                        "return_count": len(returns), "open_incidents": len(incidents)},
            "record_counts": {"customers": len({item.customer_ref for item in orders if item.customer_ref}),
                              "orders": len(orders), "order_items": len(order_items),
                              "products": len(products), "inventory_snapshots": len(inventory),
                              "returns": len(returns), "payments": len(payments),
                              "refunds": len(refunds), "settlements": len(settlements),
                              "bank_transactions": len(bank)},
            "connections": {"csv": "imported" if imports else "awaiting_import",
                            "shopify": (connection(db, workspace_id, "shopify").status
                                        if connection(db, workspace_id, "shopify") else "not_connected"),
                            "razorpay": (connection(db, workspace_id, "razorpay").status
                                         if connection(db, workspace_id, "razorpay") else "not_connected")},
            "orders": [{"id": item.id, "external_id": item.external_id, "status": item.status,
                        "channel": item.channel, "total_paise": item.total_paise,
                        "source": item.source_system, "import_batch_id": item.import_batch_id}
                       for item in sorted(orders, key=lambda x: x.placed_at, reverse=True)[:30]],
            "products": [{"id": item.id, "external_id": item.external_id,
                          "sku": item.sku, "name": item.name, "price_paise": item.price_paise}
                         for item in sorted(products, key=lambda x: x.sku)[:100]],
            "inventory": [{"id": item.id, "external_id": item.external_id,
                           "sku": item.sku, "location": item.location, "sellable": item.sellable,
                           "reserved": item.reserved, "quarantined": item.quarantined,
                           "observed_at": item.observed_at.isoformat(), "import_batch_id": item.import_batch_id}
                          for item in sorted(latest.values(), key=lambda x: x.sku)[:100]],
            "returns": [{"id": item.id, "external_id": item.external_id,
                         "sku": item.sku, "quantity": item.quantity,
                         "condition": item.condition, "status": item.status}
                        for item in sorted(returns, key=lambda x: x.recorded_at, reverse=True)[:100]],
            "incidents": [incident_data(item) for item in sorted(incidents, key=lambda x: x.created_at, reverse=True)[:30]],
            "recent_imports": [{"id": item.id, "kind": item.kind, "imported_count": item.imported_count,
                                "status": item.status} for item in imports]}


@router.get("/incidents")
def incidents(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    records = db.scalars(select(LiveIncident).where(LiveIncident.workspace_id == workspace_id)
                         .order_by(LiveIncident.created_at.desc()).limit(200)).all()
    return [incident_data(item) for item in records]


@router.get("/investigations")
def investigations(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    records = db.scalars(select(LiveInvestigation).where(LiveInvestigation.workspace_id == workspace_id)
                         .order_by(LiveInvestigation.created_at.desc()).limit(100)).all()
    return [{"id": item.id, "incident_id": item.incident_id, "mode": item.mode,
             "finding": item.finding, "evidence": item.evidence, "tool_trace": item.tool_trace,
             "proposals": proposals_from_trace(item.tool_trace),
             "created_at": item.created_at.isoformat()} for item in records]


@router.get("/incidents/{incident_id}")
def incident_detail(workspace_id: str, incident_id: str, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    record = db.get(LiveIncident, incident_id)
    if record is None or record.workspace_id != workspace_id:
        raise HTTPException(404, "Incident not found")
    findings = db.scalars(select(LiveInvestigation).where(LiveInvestigation.workspace_id == workspace_id,
                                                          LiveInvestigation.incident_id == incident_id)
                          .order_by(LiveInvestigation.created_at.desc())).all()
    reviews = db.scalars(select(AuditRecord).where(AuditRecord.workspace_id == workspace_id,
                                                    AuditRecord.target_id == incident_id,
                                                    AuditRecord.action == "incident_review")
                         .order_by(AuditRecord.created_at.desc())).all()
    conclusions = db.scalars(select(AuditRecord).where(AuditRecord.workspace_id == workspace_id,
                                                        AuditRecord.target_id == incident_id,
                                                        AuditRecord.action == "incident_concluded")
                             .order_by(AuditRecord.created_at.desc())).all()
    return {**incident_data(record), "sources": incident_sources(db, workspace_id, record.evidence),
            "reviews": [{"status": item.detail["status"], "note": item.detail["note"],
                         "reviewer_id": item.user_id, "created_at": item.created_at.isoformat()}
                        for item in reviews],
            "conclusions": [{"outcome": item.detail["outcome"], "note": item.detail["note"],
                             "reviewer_id": item.user_id, "created_at": utc(item.created_at).isoformat()}
                            for item in conclusions],
            "investigations": [{"id": item.id, "mode": item.mode, "finding": item.finding,
                                "evidence": item.evidence, "tool_trace": item.tool_trace,
                                "proposals": proposals_from_trace(item.tool_trace),
                                "structured": structured_investigation(record, item),
                                "created_at": item.created_at.isoformat()} for item in findings]}


@router.get("/incidents/{incident_id}/evidence-graph")
def incident_evidence_graph(workspace_id: str, incident_id: str,
                            user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    record = db.get(LiveIncident, incident_id)
    if record is None or record.workspace_id != workspace_id:
        raise HTTPException(404, "Incident not found")
    return evidence_graph(db, record)


class ReviewInput(BaseModel):
    status: str
    note: str = Field(min_length=3, max_length=2000)


class ConclusionInput(BaseModel):
    outcome: str
    note: str = Field(min_length=3, max_length=2000)


@router.post("/incidents/{incident_id}/review", dependencies=[Depends(same_origin)])
def review_incident(workspace_id: str, incident_id: str, data: ReviewInput,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    if data.status not in {"acknowledged", "reviewed", "open"}:
        raise HTTPException(400, "Choose acknowledged, reviewed, or open")
    record = db.get(LiveIncident, incident_id)
    if record is None or record.workspace_id != workspace_id:
        raise HTTPException(404, "Incident not found")
    record.status = data.status
    db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id, action="incident_review",
                       target_id=incident_id, detail={"status": data.status, "note": data.note.strip()}))
    db.commit()
    return incident_data(record)


@router.post("/incidents/{incident_id}/conclude", dependencies=[Depends(same_origin)])
def conclude_incident(workspace_id: str, incident_id: str, data: ConclusionInput,
                      user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    if data.outcome not in {"explained", "resolved_by_evidence", "needs_followup"}:
        raise HTTPException(400, "Choose explained, resolved_by_evidence, or needs_followup")
    record = db.get(LiveIncident, incident_id)
    if record is None or record.workspace_id != workspace_id:
        raise HTTPException(404, "Incident not found")
    if not db.scalar(select(LiveInvestigation.id).where(
        LiveInvestigation.workspace_id == workspace_id,
        LiveInvestigation.incident_id == incident_id)):
        raise HTTPException(409, "Investigate source evidence before recording a conclusion")
    record.status = data.outcome
    db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id,
                       action="incident_concluded", target_id=incident_id,
                       detail={"outcome": data.outcome, "note": data.note.strip(),
                               "evidence_ids": [item["id"] for item in record.evidence]}))
    db.commit()
    return incident_data(record)


@router.post("/incidents/{incident_id}/investigate", dependencies=[Depends(same_origin)])
def investigate_incident(workspace_id: str, incident_id: str, user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    check_analysis_budget(user.id, workspace_id, "investigate")
    record = db.get(LiveIncident, incident_id)
    if record is None or record.workspace_id != workspace_id:
        raise HTTPException(404, "Incident not found")
    finding = investigate(db, record)
    db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id, action="investigation_run",
                       target_id=record.id, detail={"mode": finding.mode}))
    db.commit()
    return {"id": finding.id, "mode": finding.mode, "finding": finding.finding,
            "evidence": finding.evidence, "tool_trace": finding.tool_trace,
            "structured": structured_investigation(record, finding),
            "proposals": proposals_from_trace(finding.tool_trace)}


@router.get("/sources/{kind}/{record_id}")
def get_source(workspace_id: str, kind: str, record_id: str,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    if kind not in KINDS:
        raise HTTPException(404, "Source type not found")
    try:
        source = source_record(db, workspace_id, SourceArgs(kind=kind, id=record_id))
        history = db.scalars(select(Observation).where(
            Observation.workspace_id == workspace_id,
            Observation.normalized_record_id == record_id)
            .order_by(Observation.ingested_at.desc()).limit(50)).all()
        source["observation_refs"] = [{"id": item.id, "event_type": item.event_type,
                                        "ingested_at": utc(item.ingested_at).isoformat(),
                                        "import_batch_id": item.import_batch_id,
                                        "webhook_delivery_id": item.webhook_delivery_id}
                                       for item in history]
        return source
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/money")
def money(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    result = []
    payments = db.scalars(select(Payment).where(Payment.workspace_id == workspace_id)).all()
    all_settlements = db.scalars(select(Settlement).where(Settlement.workspace_id == workspace_id)).all()
    all_refunds = db.scalars(select(Refund).where(Refund.workspace_id == workspace_id)).all()
    all_bank = db.scalars(select(BankTransaction).where(BankTransaction.workspace_id == workspace_id)).all()
    settlements_by_payment: dict[str, list[Settlement]] = defaultdict(list)
    refunds_by_payment: dict[str, list[Refund]] = defaultdict(list)
    bank_by_settlement: dict[str, list[BankTransaction]] = defaultdict(list)
    refund_by_id = {item.id: item for item in all_refunds}
    for item in all_settlements:
        if item.payment_id:
            settlements_by_payment[item.payment_id].append(item)
    for item in all_refunds:
        if item.payment_id:
            refunds_by_payment[item.payment_id].append(item)
    for item in all_bank:
        if item.settlement_id:
            bank_by_settlement[item.settlement_id].append(item)
    for payment in payments:
        settlements = settlements_by_payment[payment.id]
        refunds = refunds_by_payment[payment.id]
        for settlement in settlements or [None]:
            prior = refund_by_id.get(settlement.prior_refund_id) if settlement and settlement.prior_refund_id else None
            expected = (payment.amount_paise - payment.fee_paise - payment.fee_tax_paise -
                        sum(item.amount_paise for item in refunds if not prior or item.id != prior.id) -
                        (prior.amount_paise if prior else 0))
            bank = bank_by_settlement[settlement.id] if settlement else []
            bank_total = sum(item.amount_paise for item in bank)
            status = ("provider_settlement_unlinked" if not settlement and payment.source_system == "razorpay" else
                      "awaiting_settlement" if not settlement else
                      "provider_difference" if expected != settlement.amount_paise else
                      "awaiting_bank_import" if not bank else
                      "bank_difference" if bank_total != settlement.amount_paise else "matched")
            adjustments = [{"reason_code": "RECORDED_FEE", "amount_paise": -payment.fee_paise,
                            "source": {"kind": "payment", "id": payment.id}, "detail": "Recorded gateway fee"},
                           {"reason_code": "RECORDED_FEE_TAX", "amount_paise": -payment.fee_tax_paise,
                            "source": {"kind": "payment", "id": payment.id}, "detail": "Recorded tax on gateway fee"}]
            adjustments += [{"reason_code": "CROSS_PERIOD_REFUND" if prior and item.id == prior.id else "REFUND",
                             "amount_paise": -item.amount_paise,
                             "source": {"kind": "refund", "id": item.id}, "detail": "Linked refund"}
                            for item in refunds if not prior or item.id != prior.id]
            if prior:
                adjustments.append({"reason_code": "CROSS_PERIOD_REFUND", "amount_paise": -prior.amount_paise,
                                    "source": {"kind": "refund", "id": prior.id},
                                    "detail": "Settlement explicitly links a prior refund"})
            reason_codes = []
            if settlement and settlement.prior_refund_id and not prior:
                reason_codes.append("MISSING_SOURCE_DATA")
            if prior:
                reason_codes.append("CROSS_PERIOD_REFUND")
            if not settlement:
                reason_codes.append("MISSING_SOURCE_DATA")
            elif expected != settlement.amount_paise:
                reason_codes.append("UNEXPLAINED_VARIANCE")
            if settlement and not bank:
                reason_codes.append("MISSING_BANK_CREDIT")
            elif settlement and bank_total != settlement.amount_paise:
                reason_codes.append("UNEXPLAINED_VARIANCE")
            result.append({"payment": {"id": payment.id, "external_id": payment.external_id,
                                        "amount_paise": payment.amount_paise, "fee_paise": payment.fee_paise,
                                        "fee_tax_paise": payment.fee_tax_paise, "import_batch_id": payment.import_batch_id},
                           "refunds": [{"id": item.id, "external_id": item.external_id,
                                        "amount_paise": item.amount_paise} for item in refunds],
                           "prior_refund": {"id": prior.id, "external_id": prior.external_id,
                                            "amount_paise": prior.amount_paise} if prior else None,
                           "settlement": {"id": settlement.id, "external_id": settlement.external_id,
                                          "amount_paise": settlement.amount_paise} if settlement else None,
                           "bank_transactions": [{"id": item.id, "external_id": item.external_id,
                                                  "amount_paise": item.amount_paise} for item in bank],
                           "expected_paise": expected, "bank_total_paise": bank_total, "status": status})
            result[-1].update({"adjustments": adjustments,
                               "provider_variance_paise": settlement.amount_paise - expected if settlement else None,
                               "bank_variance_paise": bank_total - settlement.amount_paise if settlement and bank else None,
                               "reason_codes": list(dict.fromkeys(reason_codes)),
                               "source_path": ([{"kind": "order", "id": payment.order_id}] if payment.order_id else []) +
                                              [{"kind": "payment", "id": payment.id}] +
                                              [{"kind": "refund", "id": item.id} for item in refunds] +
                                              ([{"kind": "refund", "id": prior.id}] if prior and prior.id not in {item.id for item in refunds} else []) +
                                              ([{"kind": "settlement", "id": settlement.id}] if settlement else []) +
                                              [{"kind": "bank_transaction", "id": item.id} for item in bank]})
    return result


@router.get("/money/razorpay-settlements")
def razorpay_settlements(workspace_id: str, user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    settlements = db.scalars(select(Settlement).where(
        Settlement.workspace_id == workspace_id, Settlement.source_system == "razorpay")
        .order_by(Settlement.settled_at.desc()).limit(100)).all()
    result = []
    for item in settlements:
        observations = db.scalars(select(Observation).where(
            Observation.workspace_id == workspace_id,
            Observation.source_system == "razorpay",
            Observation.object_kind == "settlement_recon",
            Observation.correlation_id == item.external_id).order_by(Observation.ingested_at)).all()
        latest = {line.provider_object_id: line for line in observations}
        lines = [{"id": line.id, "entity_id": line.payload["entity_id"],
                  "type": line.payload["type"], "credit_paise": line.payload["credit"],
                  "debit_paise": line.payload["debit"], "payment_id": line.payload.get("payment_id")}
                 for line in latest.values()]
        calculated = sum(line["credit_paise"] - line["debit_paise"] for line in lines)
        bank = db.scalars(select(BankTransaction).where(BankTransaction.workspace_id == workspace_id,
                                                        BankTransaction.settlement_id == item.id)).all()
        bank_total = sum(row.amount_paise for row in bank)
        status = ("recon_unavailable" if not lines else
                  "provider_difference" if calculated != item.amount_paise else
                  "awaiting_bank_import" if not bank else
                  "bank_difference" if bank_total != item.amount_paise else "matched")
        result.append({"id": item.id, "external_id": item.external_id, "utr": item.raw.get("utr"),
                       "amount_paise": item.amount_paise, "settled_at": item.settled_at.isoformat(),
                       "calculated_paise": calculated if lines else None,
                       "bank_total_paise": bank_total, "bank_external_ids": [row.external_id for row in bank],
                       "status": status, "lines": lines})
    return result


class ScenarioRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=120)
    incident_id: str | None = None
    seed: int = Field(default=2026, ge=0, le=2_147_483_647)
    demand: int = Field(default=30, ge=1, le=500)
    demand_mode: str = Field(default="manual", pattern="^(manual|observed)$")
    horizon_hours: int = Field(default=24, ge=1, le=168)
    disruption_units: int = Field(default=0, ge=0, le=100000)
    unit_multiplier: int = Field(default=1, ge=1, le=5)
    supplier_lead_time: int = Field(default=0, ge=0, le=500)
    restock_units: int = Field(default=0, ge=0, le=100000)
    inspection_delay: int = Field(default=0, ge=0, le=500)
    fulfillment_cost_paise: int = Field(default=0, ge=0, le=100000)
    expedited_delivery_cost_paise: int = Field(default=0, ge=0, le=100000)
    unfulfilled_cost_paise: int = Field(default=0, ge=0, le=100000)
    return_processing_time: int = Field(default=0, ge=0, le=500)
    inspection_result: str = Field(default="failed", pattern="^(passed|failed|hold)$")
    supplier_available: bool = True
    procurement_unit_cost_paise: int = Field(default=0, ge=0, le=100000)
    holding_cost_paise_per_unit_hour: int = Field(default=0, ge=0, le=100000)
    return_inspection_cost_paise: int = Field(default=0, ge=0, le=100000)


def scenario_options(data: dict) -> dict:
    defaults = {"inspection_result": "failed", "supplier_available": True}
    return {key: data.get(key, defaults.get(key, 0)) for key in (
        "supplier_lead_time", "restock_units", "inspection_delay", "fulfillment_cost_paise",
        "expedited_delivery_cost_paise", "unfulfilled_cost_paise", "return_processing_time",
        "inspection_result", "supplier_available", "procurement_unit_cost_paise",
        "holding_cost_paise_per_unit_hour", "return_inspection_cost_paise")}


def scenario_demand(state: dict, sku: str, mode: str, horizon_hours: int,
                    seed: int) -> tuple[dict | None, list[dict] | None]:
    if mode == "manual":
        return None, None
    model = demand_model(state, sku, horizon_hours)
    if model["status"] != "ready":
        raise ValueError(model["message"])
    return model, modeled_schedule(model, seed)


class SnapshotRequest(BaseModel):
    observed_cutoff: datetime | None = None


class BacktestRequest(BaseModel):
    cutoff: datetime
    horizon_hours: int = Field(ge=1, le=720)


@router.post("/backtests", dependencies=[Depends(same_origin)])
def run_backtest(workspace_id: str, data: BacktestRequest,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    check_analysis_budget(user.id, workspace_id, "backtest")
    history = db.scalars(select(Observation).where(Observation.workspace_id == workspace_id)
                         .order_by(Observation.ingested_at, Observation.id).limit(20000)).all()
    try:
        result = evaluate_order_backtest(history, data.cutoff, data.horizon_hours)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result["status"] == "evaluated":
        audit = AuditRecord(workspace_id=workspace_id, user_id=user.id, action="backtest_run",
                            target_id=workspace_id, detail=result)
        db.add(audit)
        db.commit()
        result["run_id"] = audit.id
    return result


@router.post("/snapshots", dependencies=[Depends(same_origin)])
def create_snapshot(workspace_id: str, data: SnapshotRequest | None = None,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    try:
        if data and data.observed_cutoff and data.observed_cutoff.tzinfo is None:
            raise ValueError("Snapshot timestamp needs a timezone")
        state, digest = freeze_state(db, workspace_id, data.observed_cutoff if data else None)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    snapshot = LiveSnapshot(workspace_id=workspace_id, created_by=user.id,
                            source_state=state, source_digest=digest, schema_version=state["schema_version"],
                            observed_cutoff=datetime.fromisoformat(state["observed_cutoff"]),
                            assumptions=state["assumptions"], limitations=state["limitations"])
    db.add(snapshot)
    db.flush()
    db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id, action="snapshot_created",
                       target_id=snapshot.id, detail={"source_digest": digest}))
    db.commit()
    return {"id": snapshot.id, "source_digest": digest, "source_record_count":
            len(state["source_refs"]), "observed_cutoff": state["observed_cutoff"],
            "assumptions": state["assumptions"], "limitations": state["limitations"],
            "created_at": snapshot.created_at.isoformat()}


@router.get("/snapshots")
def snapshots(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    records = db.scalars(select(LiveSnapshot).where(LiveSnapshot.workspace_id == workspace_id)
                         .order_by(LiveSnapshot.created_at.desc()).limit(100)).all()
    return [{"id": item.id, "source_digest": item.source_digest,
             "schema_version": item.schema_version,
             "observed_cutoff": utc(item.observed_cutoff).isoformat() if item.observed_cutoff else None,
             "assumptions": item.assumptions, "limitations": item.limitations,
             "created_at": item.created_at.isoformat()} for item in records]


@router.get("/snapshots/{snapshot_id}/demand-model")
def snapshot_demand_model(workspace_id: str, snapshot_id: str, sku: str,
                          horizon_hours: int = 24, user: User = Depends(current_user),
                          db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    snapshot = db.get(LiveSnapshot, snapshot_id)
    if not snapshot or snapshot.workspace_id != workspace_id:
        raise HTTPException(404, "Snapshot not found")
    if sku not in {row["sku"] for row in snapshot.source_state["products"]}:
        raise HTTPException(400, "SKU is absent from this observed snapshot")
    try:
        return demand_model(snapshot.source_state, sku, horizon_hours)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/snapshots/{snapshot_id}/compare", dependencies=[Depends(same_origin)])
def compare_snapshot(workspace_id: str, snapshot_id: str, request: ScenarioRequest,
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    check_analysis_budget(user.id, workspace_id, "compare")
    snapshot = db.get(LiveSnapshot, snapshot_id)
    if snapshot is None or snapshot.workspace_id != workspace_id:
        raise HTTPException(404, "Snapshot not found")
    if request.incident_id:
        linked_incident = db.get(LiveIncident, request.incident_id)
        if not linked_incident or linked_incident.workspace_id != workspace_id:
            raise HTTPException(404, "Incident not found")
    try:
        before_state, _ = freeze_state(db, workspace_id)
        model, schedule = scenario_demand(snapshot.source_state, request.sku, request.demand_mode,
                                          request.horizon_hours, request.seed)
        demand = len(schedule) if schedule is not None else request.demand
        branches = simulate_branches(snapshot.source_state, request.sku, request.seed,
                                     demand, request.disruption_units, request.unit_multiplier,
                                     **scenario_options(request.model_dump()), demand_schedule=schedule)
        baseline, recovery = branches[:2]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = LiveScenario(workspace_id=workspace_id, snapshot_id=snapshot.id,
                          kind="inventory_quantity_dispute", seed=request.seed,
                          baseline=baseline, recovery=recovery, branches=branches,
                          disruption={"sku": request.sku, "incident_id": request.incident_id,
                                      "unavailable_units": request.disruption_units,
                                      "demand": demand, "demand_mode": request.demand_mode,
                                      "horizon_hours": request.horizon_hours,
                                      "demand_model": model, "unit_multiplier": request.unit_multiplier,
                                      **scenario_options(request.model_dump())},
                          assumptions=[("Observed SKU order quantities and seven-day order rate drive demand events" if model else
                                        "Each hypothetical demand event requests 1–3 units multiplied by the selected estimate"),
                                       "Unit price is the observed product price; revenue is a forecast",
                                       "Simulation clock, inspection delay, and supplier lead time use hours",
                                       "Disputed units require the selected simulated inspection result",
                                       "Conservative recovery accepts whole orders only; the alternative can partially fulfill",
                                       "A replacement supplier delivers only when availability is enabled",
                                       "Unfilled orders are lost; they are not queued or called delayed",
                                       "Modeled costs use integer paise and include entered procurement and inspection costs",
                                       "Fulfillment does not write to live orders, inventory, or providers"])
    db.add(result)
    db.flush()
    db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id, action="scenario_compared",
                       target_id=result.id, detail={"snapshot_id": snapshot.id, "sku": request.sku}))
    after_state, _ = freeze_state(db, workspace_id)
    db.commit()
    return {"id": result.id, "snapshot_id": snapshot.id, "source_digest": snapshot.source_digest,
            "kind": result.kind, "seed": result.seed, "baseline": baseline, "recovery": recovery,
            "branches": branches, "disruption": result.disruption,
            "demand_model": model,
            "assumptions": result.assumptions,
            "source_records_unchanged": {k: v for k, v in before_state.items() if k != "observed_cutoff"} ==
                                         {k: v for k, v in after_state.items() if k != "observed_cutoff"}}


@router.get("/scenarios")
def scenarios(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    records = db.scalars(select(LiveScenario).where(LiveScenario.workspace_id == workspace_id)
                         .order_by(LiveScenario.created_at.desc()).limit(100)).all()
    return [{"id": item.id, "snapshot_id": item.snapshot_id, "kind": item.kind,
             "seed": item.seed, "baseline": item.baseline, "recovery": item.recovery,
             "branches": item.branches, "disruption": item.disruption,
             "demand_model": (item.disruption or {}).get("demand_model"),
             "assumptions": item.assumptions,
             "created_at": item.created_at.isoformat()} for item in records]


@router.get("/scenarios/{scenario_id}/replay")
def replay_scenario(workspace_id: str, scenario_id: str,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    item = db.get(LiveScenario, scenario_id)
    if not item or item.workspace_id != workspace_id:
        raise HTTPException(404, "Scenario not found")
    reviews = db.scalars(select(AuditRecord).where(AuditRecord.workspace_id == workspace_id,
                                                  AuditRecord.target_id == scenario_id,
                                                  AuditRecord.action.in_(["scenario_reviewed", "scenario_verified"]))
                         .order_by(AuditRecord.created_at)).all()
    return {"id": item.id, "kind": "hypothetical_simulation", "snapshot_id": item.snapshot_id,
            "seed": item.seed,
            "source_digest": db.get(LiveSnapshot, item.snapshot_id).source_digest,
            "branches": item.branches or [item.baseline, item.recovery],
            "first_divergence": find_first_branch_divergence(item.branches or [item.baseline, item.recovery]),
            "disruption": item.disruption, "assumptions": item.assumptions,
            "reviews": [{"action": row.action, "detail": row.detail,
                         "created_at": utc(row.created_at).isoformat()} for row in reviews]}


@router.get("/scenarios/{scenario_id}/receipt.json")
def scenario_receipt_json(workspace_id: str, scenario_id: str, incident_id: str | None = None,
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    scenario = db.get(LiveScenario, scenario_id)
    if not scenario or scenario.workspace_id != workspace_id:
        raise HTTPException(404, "Scenario not found")
    linked_id = scenario.disruption.get("incident_id") if scenario.disruption else None
    if incident_id and linked_id and incident_id != linked_id:
        raise HTTPException(409, "Receipt incident does not match the saved scenario")
    try:
        return receipt_data(db, workspace_id, scenario, linked_id or incident_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/scenarios/{scenario_id}/receipt", response_class=HTMLResponse)
def scenario_receipt_html(workspace_id: str, scenario_id: str, incident_id: str | None = None,
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
    return HTMLResponse(render_receipt(scenario_receipt_json(workspace_id, scenario_id,
                                                              incident_id, user, db)),
                        headers={"Cache-Control": "private, no-store"})


class ScenarioReview(BaseModel):
    branch_index: int = Field(ge=0, le=9)
    note: str = Field(min_length=3, max_length=2000)


@router.post("/scenarios/{scenario_id}/review", dependencies=[Depends(same_origin)])
def review_scenario(workspace_id: str, scenario_id: str, data: ScenarioReview,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    item = db.get(LiveScenario, scenario_id)
    if not item or item.workspace_id != workspace_id:
        raise HTTPException(404, "Scenario not found")
    branches = item.branches or [item.baseline, item.recovery]
    if data.branch_index >= len(branches):
        raise HTTPException(400, "Choose a saved branch")
    detail = {"branch_index": data.branch_index, "label": branches[data.branch_index]["label"],
              "note": data.note.strip(), "status": "preferred_for_review"}
    db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id,
                       action="scenario_reviewed", target_id=item.id, detail=detail))
    db.commit()
    return detail


@router.post("/scenarios/{scenario_id}/verify", dependencies=[Depends(same_origin)])
def verify_scenario(workspace_id: str, scenario_id: str,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    item = db.get(LiveScenario, scenario_id)
    if not item or item.workspace_id != workspace_id:
        raise HTTPException(404, "Scenario not found")
    data = item.disruption or {}
    if not all(key in data for key in ("sku", "unavailable_units", "demand", "unit_multiplier")):
        raise HTTPException(409, "This saved scenario has no replay parameters")
    snapshot = db.get(LiveSnapshot, item.snapshot_id)
    replayed = simulate_branches(snapshot.source_state, data["sku"], item.seed,
                                 data["demand"], data["unavailable_units"], data["unit_multiplier"],
                                 **scenario_options(data),
                                 demand_schedule=scenario_demand(snapshot.source_state, data["sku"],
                                                                 data.get("demand_mode", "manual"),
                                                                 data.get("horizon_hours", 24), item.seed)[1])
    verified = replayed == item.branches
    digest = hashlib.sha256(json.dumps(replayed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    detail = {"verified": verified, "branch_digest": digest,
              "source_digest": snapshot.source_digest, "branch_count": len(replayed),
              "live_records_modified": False}
    db.add(AuditRecord(workspace_id=workspace_id, user_id=user.id,
                       action="scenario_verified", target_id=item.id, detail=detail))
    db.commit()
    return detail


@router.get("/scenarios/{scenario_id}/difference")
def scenario_difference(workspace_id: str, scenario_id: str, left: int = 0, right: int = 1,
                        metric: str = "unfilled_attempts", user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    item = db.get(LiveScenario, scenario_id)
    if not item or item.workspace_id != workspace_id:
        raise HTTPException(404, "Scenario not found")
    try:
        return explain_metric_difference(item.branches, left, right, metric)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class SensitivityRequest(BaseModel):
    variable: str
    values: list[int] = Field(min_length=1, max_length=9)
    seeds: list[int] = Field(min_length=1, max_length=12)


@router.post("/scenarios/{scenario_id}/sensitivity", dependencies=[Depends(same_origin)])
def scenario_sensitivity(workspace_id: str, scenario_id: str, data: SensitivityRequest,
                         user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    check_analysis_budget(user.id, workspace_id, "sensitivity")
    item = db.get(LiveScenario, scenario_id)
    if not item or item.workspace_id != workspace_id:
        raise HTTPException(404, "Scenario not found")
    if data.variable not in {"demand", "unavailable_units", "unit_multiplier",
                             *(scenario_options({}).keys() - {"inspection_result", "supplier_available"})}:
        raise HTTPException(400, "Unsupported assumption")
    if data.variable == "demand" and item.disruption.get("demand_mode") == "observed":
        raise HTTPException(400, "Observed demand is derived from snapshot history; vary other assumptions or use manual demand")
    if any(seed < 0 or seed > 2_147_483_647 for seed in data.seeds):
        raise HTTPException(400, "Seeds must be between 0 and 2147483647")
    snapshot = db.get(LiveSnapshot, item.snapshot_id)
    rows = []
    for value in data.values:
        params = dict(item.disruption)
        params[data.variable] = value
        try:
            request = ScenarioRequest(sku=params["sku"], demand=params["demand"],
                                      demand_mode=params.get("demand_mode", "manual"),
                                      horizon_hours=params.get("horizon_hours", 24),
                                      seed=item.seed, disruption_units=params["unavailable_units"],
                                      unit_multiplier=params["unit_multiplier"], **scenario_options(params))
            trials = []
            for seed in data.seeds:
                _, schedule = scenario_demand(snapshot.source_state, request.sku,
                                              request.demand_mode, request.horizon_hours, seed)
                trials.append(simulate_branches(snapshot.source_state, request.sku, seed,
                                                len(schedule) if schedule is not None else request.demand,
                                                request.disruption_units, request.unit_multiplier,
                                                **scenario_options(request.model_dump()),
                                                demand_schedule=schedule))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        summaries = []
        for index in range(3):
            results = [trial[index]["unfilled_attempts"] for trial in trials]
            summaries.append({"branch_index": index, "label": trials[0][index]["label"],
                              "mean_unfilled_attempts": sum(results) / len(results),
                              "min_unfilled_attempts": min(results), "max_unfilled_attempts": max(results)})
        rows.append({"value": value, "branches": summaries})
    return {"scenario_id": item.id, "variable": data.variable, "repetitions": len(data.seeds),
            "seeds": data.seeds, "rows": rows, "note": "Ranges span tested seeds; they are not confidence intervals"}


@router.get("/observations")
def observations(workspace_id: str, limit: int = 100, before: datetime | None = None,
                 before_id: str | None = None,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    if limit < 1 or limit > 500:
        raise HTTPException(400, "Limit must be between 1 and 500")
    query = select(Observation).where(Observation.workspace_id == workspace_id)
    if before and before_id:
        raise HTTPException(400, "Use either before or before_id")
    if before_id:
        cursor = db.get(Observation, before_id)
        if not cursor or cursor.workspace_id != workspace_id:
            raise HTTPException(404, "Observation not found")
        query = query.where(or_(Observation.ingested_at < cursor.ingested_at,
                                and_(Observation.ingested_at == cursor.ingested_at, Observation.id < cursor.id)))
    if before:
        if before.tzinfo is None:
            raise HTTPException(400, "History timestamp needs a timezone")
        query = query.where(Observation.ingested_at < before)
    records = db.scalars(query.order_by(Observation.ingested_at.desc(), Observation.id.desc()).limit(limit)).all()
    return [{"id": item.id, "kind": item.object_kind, "source_system": item.source_system,
             "provider_object_id": item.provider_object_id, "event_type": item.event_type,
             "external_at": item.external_at.isoformat() if item.external_at else None,
             "ingested_at": utc(item.ingested_at).isoformat(), "schema_version": item.schema_version,
             "correlation_id": item.correlation_id, "causation_id": item.causation_id,
             "previous_observation_id": item.previous_observation_id,
             "import_batch_id": item.import_batch_id, "webhook_delivery_id": item.webhook_delivery_id,
             "normalized_record_id": item.normalized_record_id,
             "idempotency_key": item.idempotency_key} for item in records]


@router.get("/observations/{observation_id}")
def observation_detail(workspace_id: str, observation_id: str,
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    item = db.get(Observation, observation_id)
    if not item or item.workspace_id != workspace_id:
        raise HTTPException(404, "Observation not found")
    return {"id": item.id, "source_system": item.source_system, "kind": item.object_kind,
            "provider_object_id": item.provider_object_id, "event_type": item.event_type,
            "ingested_at": utc(item.ingested_at).isoformat(), "external_at": utc(item.external_at).isoformat() if item.external_at else None,
            "previous_observation_id": item.previous_observation_id,
            "import_batch_id": item.import_batch_id, "webhook_delivery_id": item.webhook_delivery_id,
            "normalized_record_id": item.normalized_record_id, "payload": item.payload}


@router.get("/audit")
def audit_history(workspace_id: str, limit: int = 100,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    if limit < 1 or limit > 500:
        raise HTTPException(400, "Limit must be between 1 and 500")
    records = db.scalars(select(AuditRecord).where(AuditRecord.workspace_id == workspace_id)
                         .order_by(AuditRecord.created_at.desc()).limit(limit)).all()
    return [{"id": item.id, "action": item.action, "target_id": item.target_id,
             "user_id": item.user_id, "detail": item.detail,
             "created_at": utc(item.created_at).isoformat()} for item in records]


@router.get("/integrations")
def integrations(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    scoped(workspace_id, user, db)
    count = db.scalar(select(func.count()).select_from(ImportBatch).where(
        ImportBatch.workspace_id == workspace_id, ImportBatch.status == "complete")) or 0
    return {"csv": {"status": "imported" if count else "awaiting_import", "completed_batches": count},
            "shopify": public_status(connection(db, workspace_id, "shopify"), configured=shopify_configured()),
            "razorpay": public_status(connection(db, workspace_id, "razorpay"),
                                       configured=bool(os.getenv("CREDENTIAL_ENCRYPTION_KEY")))}
