"""Evidence-backed checks on imported records; no synthetic events enter this queue."""
from __future__ import annotations

from collections import defaultdict
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .live_models import (BankTransaction, ImportBatch, InventorySnapshot, LiveIncident, Observation, Order,
                          Payment, Refund, Return, Settlement)


def ref(kind: str, record) -> dict:
    return {"kind": kind, "id": record.id, "external_id": record.external_id,
            "import_batch_id": record.import_batch_id}


def emit(db: Session, workspace_id: str, fingerprint: str, kind: str,
         classification: str, summary: str, evidence: list[dict]) -> None:
    record = db.scalar(select(LiveIncident).where(LiveIncident.workspace_id == workspace_id,
                                                  LiveIncident.fingerprint == fingerprint))
    if record is None:
        db.add(LiveIncident(workspace_id=workspace_id, fingerprint=fingerprint, kind=kind,
                            classification=classification, summary=summary, evidence=evidence))
    else:
        if (record.kind, record.classification, record.summary, record.evidence) != (
                kind, classification, summary, evidence):
            record.status = "open"
        record.kind = kind
        record.classification = classification
        record.summary = summary
        record.evidence = evidence


def payment_expected(db: Session, payment: Payment, prior: Refund | None) -> int:
    refunds = db.scalars(select(Refund).where(Refund.workspace_id == payment.workspace_id,
                                              Refund.payment_id == payment.id)).all()
    current_refunds = sum(item.amount_paise for item in refunds if prior is None or item.id != prior.id)
    return payment.amount_paise - payment.fee_paise - payment.fee_tax_paise - current_refunds - (prior.amount_paise if prior else 0)


def detect_workspace(db: Session, workspace_id: str) -> None:
    inventory = db.scalars(select(InventorySnapshot).where(InventorySnapshot.workspace_id == workspace_id)).all()
    groups = defaultdict(list)
    for item in inventory:
        observed = item.observed_at
        observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
        groups[(item.sku, item.location, observed)].append(item)
    for (sku, location, observed), items in groups.items():
        quantities = {(item.sellable, item.reserved, item.quarantined) for item in items}
        if len(quantities) > 1:
            emit(db, workspace_id, f"inventory:{sku}:{location}:{observed.isoformat()}",
                 "inventory_mismatch", "confirmed_inconsistency",
                 f"Imported inventory snapshots disagree for {sku} at {location} and the same observation time.",
                 [ref("inventory", item) for item in items])

    returns = db.scalars(select(Return).where(Return.workspace_id == workspace_id,
                                              Return.condition == "damaged")).all()
    by_sku = defaultdict(list)
    for item in inventory:
        by_sku[item.sku].append(item)
    for item in returns:
        snapshots = by_sku[item.sku]
        if snapshots and max(snapshots, key=lambda x: x.observed_at.replace(tzinfo=timezone.utc) if x.observed_at.tzinfo is None else x.observed_at.astimezone(timezone.utc)).quarantined < item.quantity:
            latest = max(snapshots, key=lambda x: x.observed_at.replace(tzinfo=timezone.utc) if x.observed_at.tzinfo is None else x.observed_at.astimezone(timezone.utc))
            emit(db, workspace_id, f"damaged_return:{item.id}", "damaged_return_stock",
                 "suspicious_discrepancy",
                 f"Damaged return {item.external_id} has {item.quantity} unit(s), while the latest {item.sku} inventory snapshot shows fewer quarantined units. Check event timing and location.",
                 [ref("return", item), ref("inventory", latest)])

    for order in db.scalars(select(Order).where(Order.workspace_id == workspace_id,
                                                   Order.status.in_(["failed", "fulfillment_failed"]))).all():
        emit(db, workspace_id, f"fulfillment:{order.id}", "fulfillment_failure",
             "confirmed_inconsistency", f"Order {order.external_id} is marked as failed by its source.",
             [ref("order", order)])

    payments = db.scalars(select(Payment).where(Payment.workspace_id == workspace_id)).all()
    for payment in payments:
        refunds = db.scalars(select(Refund).where(Refund.workspace_id == workspace_id,
                                                  Refund.payment_id == payment.id)).all()
        if sum(item.amount_paise for item in refunds) > payment.amount_paise:
            emit(db, workspace_id, f"refund_excess:{payment.id}", "refund_inconsistency",
                 "confirmed_inconsistency", f"Refunds linked to payment {payment.external_id} exceed its captured amount.",
                 [ref("payment", payment), *[ref("refund", item) for item in refunds]])

    for settlement in db.scalars(select(Settlement).where(Settlement.workspace_id == workspace_id)).all():
        if settlement.source_system == "razorpay" and settlement.payment_id is None:
            observations = db.scalars(select(Observation).where(
                Observation.workspace_id == workspace_id,
                Observation.source_system == "razorpay",
                Observation.object_kind == "settlement_recon",
                Observation.correlation_id == settlement.external_id)
                .order_by(Observation.ingested_at)).all()
            latest = {line.provider_object_id: line for line in observations}
            if latest:
                derived = sum(line.payload["credit"] - line.payload["debit"] for line in latest.values())
                if derived != settlement.amount_paise:
                    emit(db, workspace_id, f"razorpay_recon:{settlement.id}", "payout_difference",
                         "confirmed_inconsistency",
                         f"Razorpay settlement {settlement.external_id} differs from its provider reconciliation lines.",
                         [ref("settlement", settlement)])
            linked_bank = db.scalars(select(BankTransaction).where(
                BankTransaction.workspace_id == workspace_id,
                BankTransaction.settlement_id == settlement.id)).all()
            if linked_bank and sum(item.amount_paise for item in linked_bank) != settlement.amount_paise:
                emit(db, workspace_id, f"razorpay_bank:{settlement.id}", "payout_difference",
                     "confirmed_inconsistency",
                     f"Bank credits linked to Razorpay settlement {settlement.external_id} do not equal its provider amount.",
                     [ref("settlement", settlement), *[ref("bank_transaction", item) for item in linked_bank]])
            continue
        payment = db.get(Payment, settlement.payment_id)
        if payment is None or payment.workspace_id != workspace_id:
            continue
        prior = db.get(Refund, settlement.prior_refund_id) if settlement.prior_refund_id else None
        expected = payment_expected(db, payment, prior)
        evidence = [ref("payment", payment), ref("settlement", settlement)]
        if prior:
            evidence.append(ref("refund", prior))
        if expected != settlement.amount_paise:
            emit(db, workspace_id, f"settlement:{settlement.id}", "payout_difference",
                 "confirmed_inconsistency",
                 f"Settlement {settlement.external_id} records {settlement.amount_paise} paise; source-derived expected amount is {expected} paise.", evidence)
            continue
        bank = db.scalars(select(BankTransaction).where(BankTransaction.workspace_id == workspace_id,
                                                        BankTransaction.settlement_id == settlement.id)).all()
        if not bank:
            emit(db, workspace_id, f"settlement:{settlement.id}", "payout_timing",
                 "explained_timing_difference", f"Settlement {settlement.external_id} is internally balanced; no linked bank credit has been imported yet.", evidence)
        elif sum(item.amount_paise for item in bank) != settlement.amount_paise:
            emit(db, workspace_id, f"settlement:{settlement.id}", "payout_difference",
                 "confirmed_inconsistency", f"Bank credits linked to settlement {settlement.external_id} do not equal its source amount.",
                 evidence + [ref("bank_transaction", item) for item in bank])
        else:
            previous = db.scalar(select(LiveIncident).where(LiveIncident.workspace_id == workspace_id,
                                                             LiveIncident.fingerprint == f"settlement:{settlement.id}"))
            if previous:
                previous.status = "resolved_by_evidence"

    for bank in db.scalars(select(BankTransaction).where(BankTransaction.workspace_id == workspace_id,
                                                         BankTransaction.settlement_id.is_(None))).all():
        emit(db, workspace_id, f"unmatched_bank:{bank.id}", "unmatched_bank_credit",
             "suspicious_discrepancy", f"Bank transaction {bank.external_id} has no linked imported settlement.",
             [ref("bank_transaction", bank)])

    for batch in db.scalars(select(ImportBatch).where(ImportBatch.workspace_id == workspace_id,
                                                   ImportBatch.status == "rejected")).all():
        if any("duplicate" in error["message"].lower() or "already exists" in error["message"].lower()
               for error in batch.errors):
            emit(db, workspace_id, f"duplicate_batch:{batch.id}", "duplicate_source_conflict",
                 "suspicious_discrepancy", f"Import {batch.filename} contains a conflicting source identifier.",
                 [{"kind": "import_batch", "id": batch.id, "external_id": batch.filename}])
