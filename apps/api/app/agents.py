"""Validated read tools and evidence-grounded investigation.

The deterministic specialist supplies authoritative arithmetic. A configured
OpenAI-compatible provider may improve wording but cannot edit state or plans.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from .domain import project
from .store import events


class RelatedEventsArgs(BaseModel):
    run_id: str = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1, max_length=20)


class BusinessStateArgs(BaseModel):
    run_id: str = Field(min_length=1)
    affected_entities: dict[str, str]


class Explanation(BaseModel):
    summary: str = Field(min_length=12, max_length=600)
    evidence_event_ids: list[str] = Field(min_length=1, max_length=20)


def get_related_events(db: Any, args: RelatedEventsArgs) -> list[dict[str, Any]]:
    chosen = set(args.event_ids)
    return [{"event_id": e["event_id"], "type": e["event_type"], "payload": e["payload"],
             "causation_id": e["causation_id"], "sequence": e["sequence_number"]}
            for e in events(db, args.run_id) if e["event_id"] in chosen]


def get_business_state(db: Any, args: BusinessStateArgs) -> dict[str, Any]:
    state = project(events(db, args.run_id))
    entities = args.affected_entities
    return {"stock": state["stock"].get(entities.get("sku", "")),
            "return": state["returns"].get(entities.get("return_id", "")),
            "payment": state["payments"].get(entities.get("payment_id", "")),
            "settlement": state["settlements"].get(entities.get("settlement_id", "")),
            "supplier": state["suppliers"].get(entities.get("supplier_id", ""))}


def _format_paise(amount: int) -> str:
    return f"₹{amount/100:,.2f}"


def _deterministic_finding(kind: str, source: list[dict[str, Any]], state: dict[str, Any]) -> str:
    if kind == "damaged_return":
        ret = state["return"]
        observation = next(e for e in source if e["type"] == "ExternalStockObserved")
        return (f"Return {observation['payload']['return_id']} is {ret['status']}; the adapter claimed "
                f"{observation['payload']['claim']} stock before inspection. The authoritative stock ledger did not add that unit.")
    if kind in ("settlement_mismatch", "missing_payout"):
        payment, settlement = state["payment"], state["settlement"]
        expected = payment["captured_paise"] - payment["refunded_paise"] - payment["fee_paise"] - payment["fee_tax_paise"] - settlement["prior_refund_paise"]
        return (f"{_format_paise(payment['captured_paise'])} capture − {_format_paise(payment['fee_paise'])} fee "
                f"− {_format_paise(payment['fee_tax_paise'])} tax − {_format_paise(payment['refunded_paise'])} current refund "
                f"− {_format_paise(settlement['prior_refund_paise'])} "
                f"prior-period adjustment = {_format_paise(expected)}; bank received "
                f"{_format_paise(settlement['received_paise'])}. "
                f"{'Matched' if expected == settlement['received_paise'] else 'Unresolved variance'}.")
    surge = next(e for e in source if e["type"] == "DemandSurgeObserved")
    supplier = state["supplier"]
    stock = state["stock"]
    projected = surge["payload"]["projected_orders"]
    return (f"Projected demand {projected} units exceeds {stock['sellable']} sellable units by "
            f"{max(0,projected-stock['sellable'])}. Supplier lead time is "
            f"{supplier['lead_days']+supplier['delay_days']} days with the reported delay.")


def _model_explanation(finding: str, evidence_ids: list[str]) -> Explanation | None:
    key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    base = os.getenv("LLM_BASE_URL")
    if not (key and model and base):
        return None
    payload = {"model": model, "temperature": 0, "max_tokens": 220,
        "messages": [{"role": "system", "content": "Return JSON with summary and evidence_event_ids only. Summarize the supplied deterministic finding. Do not add facts or arithmetic."},
                     {"role": "user", "content": json.dumps({"finding": finding, "evidence_event_ids": evidence_ids})}]}
    endpoint = base.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=8) as response:
        result = json.loads(response.read())
    content = result["choices"][0]["message"]["content"]
    explanation = Explanation.model_validate_json(content)
    if not set(explanation.evidence_event_ids).issubset(evidence_ids):
        raise ValueError("Model cited unsupported evidence")
    return explanation


def investigate(db: Any, incident: dict[str, Any]) -> None:
    execution_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    event_args = RelatedEventsArgs(run_id=incident["run_id"], event_ids=incident["evidence_event_ids"])
    state_args = BusinessStateArgs(run_id=incident["run_id"], affected_entities=incident["affected_entities"])
    calls = []
    start = time.perf_counter()
    source = get_related_events(db, event_args)
    calls.append(("get_related_events", event_args.model_dump(), source, (time.perf_counter()-start)*1000, "ok"))
    start = time.perf_counter()
    state = get_business_state(db, state_args)
    calls.append(("get_business_state", state_args.model_dump(), state, (time.perf_counter()-start)*1000, "ok"))
    finding = _deterministic_finding(incident["type"], source, state)
    mode = "deterministic_demo"
    if os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL") and os.getenv("LLM_BASE_URL"):
        start = time.perf_counter()
        try:
            explanation = _model_explanation(finding, incident["evidence_event_ids"])
            if explanation:
                finding = explanation.summary
                mode = "ai_enabled"
            calls.append(("model_explanation", {"model": os.getenv("LLM_MODEL")},
                          {"validated": True}, (time.perf_counter()-start)*1000, "ok"))
        except (ValueError, ValidationError, KeyError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            calls.append(("model_explanation", {"model": os.getenv("LLM_MODEL")},
                          {"error": str(exc)[:160]}, (time.perf_counter()-start)*1000, "error"))
    db.execute("INSERT INTO agent_executions VALUES (?,?,?,?,?,?,?,?)", (execution_id,
        incident["incident_id"], "Investigator + specialist", mode,
        json.dumps([call[0] for call in calls]), finding,
        json.dumps(incident["evidence_event_ids"]), now))
    for tool_name, arguments, result, duration, status in calls:
        db.execute("INSERT INTO agent_tool_calls VALUES (?,?,?,?,?,?,?,?)", (str(uuid4()),
            execution_id, tool_name, json.dumps(arguments), json.dumps(result),
            round(duration, 2), status, now))
