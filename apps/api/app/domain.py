"""Pure, deterministic commerce projections. Money is always integer paise."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def empty_state() -> dict[str, Any]:
    return {"stock": {}, "orders": {}, "returns": {}, "payments": {}, "settlements": {}, "adjustments": {},
            "purchase_orders": {}, "authorized_recoveries": {},
            "suppliers": {}, "incidents": {}, "expenses_paise": 0, "rejected_observations": 0,
            "unfilled_demand": 0, "payout_disputes": {}}


class PolicyError(ValueError):
    pass


def apply(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Apply one validated event to a copied projection; never mutates the caller."""
    s = deepcopy(state)
    p, kind = event["payload"], event["event_type"]
    if kind == "ProductStocked":
        sku = p["sku"]
        if sku in s["stock"]:
            raise PolicyError("SKU already stocked")
        s["stock"][sku] = {"name": p["name"], "price_paise": p["price_paise"],
                            "sellable": p["quantity"], "reserved": 0, "quarantined": 0,
                            "inspection_pending": 0, "supplier": p["supplier"]}
    elif kind == "OrderCreated":
        if p["order_id"] in s["orders"]:
            raise PolicyError("Order exists")
        s["orders"][p["order_id"]] = {"sku": p["sku"], "quantity": p["quantity"],
                                      "amount_paise": p["amount_paise"], "status": "created",
                                      "channel": p["channel"]}
    elif kind == "InventoryReserved":
        order = s["orders"][p["order_id"]]
        stock = s["stock"][order["sku"]]
        if order["status"] != "created" or stock["sellable"] < order["quantity"]:
            raise PolicyError("Insufficient sellable stock")
        stock["sellable"] -= order["quantity"]
        stock["reserved"] += order["quantity"]
        order["status"] = "reserved"
    elif kind == "PaymentCaptured":
        order = s["orders"][p["order_id"]]
        if p["amount_paise"] != order["amount_paise"] or p["payment_id"] in s["payments"]:
            raise PolicyError("Invalid capture")
        s["payments"][p["payment_id"]] = {"order_id": p["order_id"],
            "captured_paise": p["amount_paise"], "refunded_paise": 0,
            "fee_paise": p["fee_paise"], "fee_tax_paise": p["fee_tax_paise"]}
    elif kind == "OrderDelivered":
        order = s["orders"][p["order_id"]]
        if order["status"] != "reserved":
            raise PolicyError("Order has no valid reservation")
        s["stock"][order["sku"]]["reserved"] -= order["quantity"]
        order["status"] = "delivered"
    elif kind == "ReturnReceived":
        order = s["orders"][p["order_id"]]
        if order["status"] != "delivered" or p["return_id"] in s["returns"] or any(r["order_id"] == p["order_id"] for r in s["returns"].values()):
            raise PolicyError("Return requires delivered order")
        s["returns"][p["return_id"]] = {"order_id": p["order_id"], "sku": order["sku"],
            "quantity": order["quantity"], "status": "inspection_pending"}
        s["stock"][order["sku"]]["inspection_pending"] += order["quantity"]
    elif kind == "ReturnInspected":
        ret = s["returns"][p["return_id"]]
        if ret["status"] != "inspection_pending":
            raise PolicyError("Return already inspected")
        stock = s["stock"][ret["sku"]]
        stock["inspection_pending"] -= ret["quantity"]
        stock["quarantined" if p["damaged"] else "sellable"] += ret["quantity"]
        ret["status"] = "quarantined" if p["damaged"] else "sellable"
    elif kind == "ExternalStockObserved":
        # Observations are evidence, never an authoritative inventory mutation.
        if p.get("claim") == "sellable" and s["returns"][p["return_id"]]["status"] == "inspection_pending":
            s["rejected_observations"] += 1
    elif kind == "RefundIssued":
        payment = s["payments"][p["payment_id"]]
        amount = p["amount_paise"]
        if amount <= 0 or payment["refunded_paise"] + amount > payment["captured_paise"]:
            raise PolicyError("Refund exceeds refundable balance")
        payment["refunded_paise"] += amount
    elif kind == "PriorPeriodRefundRecorded":
        if p["adjustment_id"] in s["adjustments"] or p["amount_paise"] <= 0:
            raise PolicyError("Invalid prior-period adjustment")
        s["adjustments"][p["adjustment_id"]] = p
    elif kind == "SettlementGenerated":
        if p["settlement_id"] in s["settlements"]:
            raise PolicyError("Settlement exists")
        if any(item["payment_id"] == p["payment_id"] for item in s["settlements"].values()):
            raise PolicyError("Payment already assigned to a settlement")
        payment = s["payments"][p["payment_id"]]
        adjustment = s["adjustments"].get(p.get("adjustment_id"))
        if p["prior_refund_paise"] and (adjustment is None or adjustment["amount_paise"] != p["prior_refund_paise"]):
            raise PolicyError("Settlement adjustment lacks source record")
        if not p["prior_refund_paise"] and p.get("adjustment_id"):
            raise PolicyError("Unexpected settlement adjustment")
        expected = payment["captured_paise"] - payment["refunded_paise"] - payment["fee_paise"] - payment["fee_tax_paise"] - p["prior_refund_paise"]
        if expected != p["expected_paise"]:
            raise PolicyError("Settlement arithmetic does not balance")
        s["settlements"][p["settlement_id"]] = {**p, "received_paise": None, "reconciled": False}
    elif kind == "SettlementReceived":
        settlement = s["settlements"][p["settlement_id"]]
        if settlement["received_paise"] is not None:
            raise PolicyError("Settlement already received")
        settlement["received_paise"] = p["amount_paise"]
        settlement["reconciled"] = p["amount_paise"] == settlement["expected_paise"]
    elif kind == "PayoutDisputeOpened":
        settlement = s["settlements"][p["settlement_id"]]
        if settlement["received_paise"] is None or settlement["reconciled"] or p["settlement_id"] in s["payout_disputes"]:
            raise PolicyError("Dispute requires unresolved settlement")
        s["payout_disputes"][p["settlement_id"]] = {"incident_id": p["incident_id"], "status": "open"}
    elif kind == "OperationalExpenseRecorded":
        if p["amount_paise"] <= 0 or not p.get("category"):
            raise PolicyError("Invalid operational expense")
        s["expenses_paise"] += p["amount_paise"]
    elif kind == "SupplierConfigured":
        if p["supplier_id"] in s["suppliers"]:
            raise PolicyError("Supplier already configured")
        s["suppliers"][p["supplier_id"]] = {"lead_days": p["lead_days"],
            "capacity": p["capacity"], "unit_cost_paise": p["unit_cost_paise"],
            "catalog_skus": p["catalog_skus"], "delay_days": 0}
    elif kind == "SupplierDelayReported":
        supplier = s["suppliers"].get(p["supplier_id"])
        if supplier is None or p["sku"] not in supplier["catalog_skus"]:
            raise PolicyError("Unknown supplier capability")
        supplier["delay_days"] = p["delay_days"]
    elif kind == "DemandUnfilled":
        s["unfilled_demand"] += 1
    elif kind == "RestockOrdered":
        if p["quantity"] <= 0 or p["cost_paise"] < 0:
            raise PolicyError("Invalid restock")
        supplier = s["suppliers"].get(p["supplier_id"])
        if supplier is None or p["sku"] not in supplier["catalog_skus"] or p["quantity"] > supplier["capacity"]:
            raise PolicyError("Supplier cannot fulfill purchase order")
        if p["lead_days"] != supplier["lead_days"] + supplier["delay_days"] or p["cost_paise"] != p["quantity"] * supplier["unit_cost_paise"]:
            raise PolicyError("Purchase order terms do not match supplier catalog")
        s["purchase_orders"][event["event_id"]] = {"sku": p["sku"], "quantity": p["quantity"],
            "received": False, "lead_days": p["lead_days"], "ordered_at": event["timestamp"]}
        s["expenses_paise"] += p["cost_paise"]
    elif kind == "RestockReceived":
        source = s["purchase_orders"].get(p.get("source_event_id"))
        if source is None or source["received"] or source["sku"] != p["sku"] or source["quantity"] != p["quantity"]:
            raise PolicyError("Stock adjustment requires source event")
        from datetime import datetime
        if (datetime.fromisoformat(event["timestamp"]) - datetime.fromisoformat(source["ordered_at"])).total_seconds() < source["lead_days"] * 86400:
            raise PolicyError("Purchase order arrived before supplier lead time")
        source["received"] = True
        s["stock"][p["sku"]]["sellable"] += p["quantity"]
    elif kind == "IncidentDetected":
        s["incidents"][p["incident_id"]] = {"type": p["type"], "status": "detected"}
    elif kind in ("ActionApproved", "ScenarioActionApplied"):
        s["authorized_recoveries"][p["incident_id"]] = True
    elif kind == "RecoveryVerified":
        if p["passed"]:
            if not s["authorized_recoveries"].get(p["incident_id"]):
                raise PolicyError("Recovery requires approval or isolated scenario authorization")
            incident_type = s["incidents"][p["incident_id"]]["type"]
            if incident_type == "damaged_return" and s["returns"][p["return_id"]]["status"] != "quarantined":
                raise PolicyError("Return remains uninspected")
            if incident_type == "settlement_mismatch" and not s["settlements"][p["settlement_id"]]["reconciled"]:
                raise PolicyError("Settlement remains unmatched")
            s["incidents"][p["incident_id"]]["status"] = "resolved"
    for stock in s["stock"].values():
        if min(stock["sellable"], stock["reserved"], stock["quarantined"], stock["inspection_pending"]) < 0:
            raise PolicyError("Negative inventory")
    return s


def project(events: list[dict[str, Any]]) -> dict[str, Any]:
    state = empty_state()
    for event in events:
        state = apply(state, event)
    return state


def metrics(state: dict[str, Any]) -> dict[str, int]:
    orders = state["orders"].values()
    payments = state["payments"].values()
    settlements = state["settlements"].values()
    return {"gross_sales_paise": sum(o["amount_paise"] for o in orders),
            "net_sales_paise": sum(p["captured_paise"] - p["refunded_paise"] for p in payments),
            "order_count": len(state["orders"]),
            "pending_orders": sum(o["status"] != "delivered" for o in orders),
            "available_stock": sum(x["sellable"] for x in state["stock"].values()),
            "reserved_stock": sum(x["reserved"] for x in state["stock"].values()),
            "active_returns": sum(x["status"] == "inspection_pending" for x in state["returns"].values()),
            "unreconciled_paise": sum(abs((x["received_paise"] or 0) - x["expected_paise"])
                                       for x in settlements if x["received_paise"] is not None and not x["reconciled"]),
            "open_incidents": sum(x["status"] != "resolved" for x in state["incidents"].values()),
            "expenses_paise": state["expenses_paise"],
            "unfilled_demand": state["unfilled_demand"],
            "payout_disputes": len(state["payout_disputes"]),
            "rejected_observations": state["rejected_observations"]}
