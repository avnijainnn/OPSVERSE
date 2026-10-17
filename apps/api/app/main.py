from __future__ import annotations

import asyncio
from html import escape
import json
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from threading import Lock

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .domain import metrics, project
from .engine import compare, decide, incident, inject, seed_world, step
from .live_auth import router as auth_router
from .live_imports import router as imports_router
from .live_api import router as live_router
from .shopify_api import drain_deliveries, router as shopify_router
from .live_models import configured as live_database_configured
from .razorpay_api import drain_razorpay_deliveries, router as razorpay_router, webhook_router as razorpay_webhook_router
from .store import connection, events, init, rows, run

_demo_budget: dict[str, deque[float]] = defaultdict(deque)
_demo_budget_lock = Lock()


def check_demo_budget(request: Request) -> None:
    """Bound expensive anonymous demo mutations on a single API instance."""
    identity = request.client.host if request.client else "unknown"
    now = time.monotonic()
    limit = int(os.getenv("DEMO_MUTATIONS_PER_HOUR", "60"))
    with _demo_budget_lock:
        recent = _demo_budget[identity]
        while recent and recent[0] < now - 3600:
            recent.popleft()
        if len(recent) >= limit:
            raise HTTPException(429, "Demo mutation limit reached; retry later")
        recent.append(now)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init()
    runner = asyncio.create_task(simulation_loop())
    provider_runner = asyncio.create_task(provider_delivery_loop())
    yield
    runner.cancel()
    provider_runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass
    try:
        await provider_runner
    except asyncio.CancelledError:
        pass


async def provider_delivery_loop() -> None:
    while True:
        if live_database_configured():
            try:
                await asyncio.to_thread(drain_deliveries)
                await asyncio.to_thread(drain_razorpay_deliveries)
            except Exception:
                # Verified receipts remain durable and are retried on the next pass.
                pass
        await asyncio.sleep(30)


def advance_due(due: dict[str, float]) -> None:
    now = time.monotonic()
    with connection() as db:
        active = rows(db, "SELECT * FROM simulation_runs WHERE status='running' AND parent_run_id IS NULL")
        active_ids = {item["run_id"] for item in active}
        for run_meta in active:
            run_id = run_meta["run_id"]
            if now >= due.get(run_id, 0):
                step(db, run_id)
                due[run_id] = now + 1.8 / run_meta["speed"]
        for run_id in list(due):
            if run_id not in active_ids:
                del due[run_id]


async def simulation_loop() -> None:
    due: dict[str, float] = {}
    while True:
        try:
            await asyncio.to_thread(advance_due, due)
        except Exception:
            # A failed tick must leave the ledger unchanged; the next cycle retries.
            pass
        await asyncio.sleep(0.25)


app = FastAPI(title="OpsVerse API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)
app.include_router(imports_router)
app.include_router(live_router)
app.include_router(shopify_router)
app.include_router(razorpay_router)
app.include_router(razorpay_webhook_router)


def execution_mode() -> str:
    return "ai_enabled" if all(os.getenv(key) for key in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")) else "deterministic_demo"


class SeedRequest(BaseModel):
    seed: int = 2026


class IncidentRequest(BaseModel):
    type: str


class ControlRequest(BaseModel):
    action: str
    speed: int | None = Field(default=None, ge=1, le=10)


class DecisionRequest(BaseModel):
    approve: bool
    expected_sequence: int


def error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409 if isinstance(exc, ValueError) else 404, detail=str(exc))


@app.get("/health")
def health():
    return {"status": "ok", "mode": execution_mode()}


@app.get("/runs")
def list_runs():
    if os.getenv("PUBLIC_SHOWCASE", "false").lower() == "true":
        # Root run IDs are private bearer capabilities in showcase mode.
        return []
    with connection(write=False) as db:
        return rows(db, "SELECT * FROM simulation_runs WHERE parent_run_id IS NULL ORDER BY created_at DESC LIMIT 20")


@app.post("/runs")
def launch(request: SeedRequest, http_request: Request):
    check_demo_budget(http_request)
    with connection() as db:
        count = db.execute("SELECT COUNT(*) FROM simulation_runs WHERE parent_run_id IS NULL").fetchone()[0]
        if count >= int(os.getenv("DEMO_MAX_ROOT_RUNS", "1000")):
            raise HTTPException(429, "Demo run capacity reached")
        db.execute("UPDATE simulation_runs SET status='paused' WHERE parent_run_id IS NULL AND status='running'")
        run_id = seed_world(db, request.seed)
        return world_data(db, run_id)


def world_data(db, run_id: str, until: int | None = None):
    meta = run(db, run_id)
    ledger = events(db, run_id, until=until)
    state = project(ledger)
    return {"run": meta, "sequence": len(ledger), "state": state, "execution_mode": execution_mode(),
            "metrics": metrics(state), "events": ledger,
            "incidents": rows(db, "SELECT * FROM incidents WHERE run_id=? ORDER BY created_at DESC", (run_id,))}


@app.get("/runs/{run_id}")
def world(run_id: str, until: int | None = None):
    try:
        with connection(write=False) as db:
            return world_data(db, run_id, until)
    except (KeyError, ValueError) as exc:
        raise error(exc)


@app.get("/runs/{run_id}/events")
def event_history(run_id: str, after: int = 0):
    with connection(write=False) as db:
        run(db, run_id)
        return events(db, run_id, after=after)


@app.post("/runs/{run_id}/control")
def control(run_id: str, request: ControlRequest):
    try:
        with connection() as db:
            run(db, run_id)
            if request.action == "step":
                step(db, run_id)
            elif request.action in ("pause", "resume"):
                db.execute("UPDATE simulation_runs SET status=? WHERE run_id=?",
                           ("paused" if request.action == "pause" else "running", run_id))
            elif request.action == "speed" and request.speed in (1, 5, 10):
                db.execute("UPDATE simulation_runs SET speed=? WHERE run_id=?", (request.speed, run_id))
            else:
                raise ValueError("Unknown control")
            return world_data(db, run_id)
    except (KeyError, ValueError) as exc:
        raise error(exc)


@app.post("/runs/{run_id}/tick")
def tick(run_id: str):
    with connection() as db:
        meta = run(db, run_id)
        if meta["status"] == "running":
            step(db, run_id)
        return world_data(db, run_id)


@app.post("/runs/{run_id}/incidents")
def inject_incident(run_id: str, request: IncidentRequest, http_request: Request):
    check_demo_budget(http_request)
    try:
        with connection() as db:
            run(db, run_id)
            return inject(db, run_id, request.type)
    except (KeyError, ValueError) as exc:
        raise error(exc)


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    try:
        with connection(write=False) as db:
            return incident(db, incident_id)
    except KeyError as exc:
        raise error(exc)


@app.get("/runs/{run_id}/traces")
def traces(run_id: str):
    with connection(write=False) as db:
        run(db, run_id)
        executions = rows(db, """SELECT a.* FROM agent_executions a JOIN incidents i
            ON i.incident_id=a.incident_id WHERE i.run_id=? ORDER BY a.created_at DESC""", (run_id,))
        for execution in executions:
            execution["tool_calls"] = rows(db, "SELECT * FROM agent_tool_calls WHERE execution_id=? ORDER BY created_at", (execution["execution_id"],))
        return executions


@app.post("/incidents/{incident_id}/compare")
def compare_incident(incident_id: str, http_request: Request):
    check_demo_budget(http_request)
    try:
        with connection() as db:
            return compare(db, incident_id)
    except (KeyError, ValueError) as exc:
        raise error(exc)


class DemoSensitivityRequest(BaseModel):
    review_delay_hours: list[int] = Field(min_length=1, max_length=5)


@app.post("/incidents/{incident_id}/sensitivity")
def demo_sensitivity(incident_id: str, request: DemoSensitivityRequest):
    try:
        with connection() as db:
            results = [compare(db, incident_id, value) for value in request.review_delay_hours]
            def recovery_hours(result: dict) -> float | None:
                history = events(db, result["third_run_id"])
                start = next((item for item in history if item["event_type"] == "IncidentDetected" and item["aggregate_id"] == incident_id), None)
                verified = next((item for item in history if item["event_type"] == "RecoveryVerified" and item["aggregate_id"] == incident_id), None)
                return ((datetime.fromisoformat(verified["timestamp"]) - datetime.fromisoformat(start["timestamp"]))
                        .total_seconds() / 3600) if start and verified else None
            return {"variable": "manual_review_delay_hours", "repetitions": 1,
                    "note": "One deterministic seed; this is an assumption sweep, not a confidence interval",
                    "rows": [{"value": value, "scenario_result_id": result["result_id"],
                              "branch_c": {"open_incidents": result["third_metrics"]["open_incidents"],
                                           "recovery_hours": recovery_hours(result),
                                           "unfilled_demand": result["third_metrics"]["unfilled_demand"],
                                           "expenses_paise": result["third_metrics"]["expenses_paise"]}}
                             for value, result in zip(request.review_delay_hours, results)]}
    except (KeyError, ValueError) as exc:
        raise error(exc)


@app.get("/incidents/{incident_id}/receipt", response_class=HTMLResponse)
def demo_receipt(incident_id: str, result_id: str | None = None):
    """Print-safe receipt for the isolated synthetic SQLite demo."""
    try:
        with connection(write=False) as db:
            data = incident(db, incident_id)
            selected = rows(db, "SELECT * FROM scenario_results WHERE incident_id=? AND result_id=?",
                            (incident_id, result_id)) if result_id else data["scenario"]
            scenario = selected[0] if selected else None
            if scenario is None:
                raise ValueError("Compare branches before exporting a receipt")
            source = run(db, data["run_id"])
            summary = [("A · No intervention", scenario["baseline_run_id"], scenario["baseline_metrics"]),
                       ("B · Immediate recovery", scenario["recovery_run_id"], scenario["recovery_metrics"])]
            if scenario.get("third_run_id") and scenario.get("third_metrics"):
                summary.append(("C · Delayed manual review", scenario["third_run_id"], scenario["third_metrics"]))
            h = lambda value: escape(str(value))
            rows_html = "".join(f"<tr><td>{h(label)}</td><td>{h(run_id)}</td><td>{metric['open_incidents']}</td>"
                                f"<td>{metric['unfilled_demand']}</td><td>{metric['expenses_paise']}</td></tr>"
                                for label, run_id, metric in summary)
            evidence_html = "".join(f"<li>{h(event['event_type'])} · {h(event['event_id'])} · {h(event['timestamp'])}</li>"
                                    for event in data["evidence_events"])
            verification = data["verification"][-1] if data["verification"] else None
            manifest = h(json.dumps({"mode": "isolated_synthetic_demo", "seed": source["seed"],
                "incident_id": incident_id, "scenario_result_id": scenario["result_id"],
                "branch_run_ids": [item[1] for item in summary], "model_version": "sqlite-event-simulator-v2"}, indent=2))
            return HTMLResponse(f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>OpsVerse synthetic Decision Receipt</title><style>body{{font:15px/1.55 system-ui;background:#f4f7f7;color:#162936;margin:0}}main{{max-width:850px;margin:auto;padding:36px}}header{{background:#102c38;color:#eef9f7;padding:28px;border-radius:10px}}small{{color:#65d4c4}}section{{background:white;padding:18px;margin:18px 0;border:1px solid #d5e0e1;border-radius:8px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #d5e0e1;text-align:left}}pre{{overflow:auto;background:#e8f1f0;padding:12px}}button{{background:#11665f;color:white;border:0;border-radius:6px;padding:10px 15px}}@media print{{button{{display:none}}main{{padding:0}}}}</style><main><header><small>OPSVERSE / SYNTHETIC DEMO</small><h1>Decision Receipt</h1><p>{h(data['type'])} · {h(incident_id)}</p></header><p>Sandbox events and outcomes only. No merchant or provider records were changed.</p><button onclick="window.print()">Print / save PDF</button><section><h2>Observed synthetic evidence</h2><ul>{evidence_html}</ul><p>{h(data['description'])}</p></section><section><h2>Investigation</h2><p>{h(data['root_cause'])}</p><p>{h(data['traces'][0]['finding'] if data['traces'] else 'No investigation')}</p><p>Proposal: {h(data['plans'][0]['action_type'] if data['plans'] else 'None')}</p></section><section><h2>Three isolated futures</h2><table><tr><th>Branch</th><th>Run ID</th><th>Open incidents</th><th>Lost demand</th><th>Cost · paise</th></tr>{rows_html}</table></section><section><h2>Decision and verification</h2><p>Status: {h(data['status'])}</p><p>Sandbox verification: {h(verification['passed'] if verification else 'Not run')}</p></section><section><h2>Reproducibility manifest</h2><pre>{manifest}</pre></section></main></html>""", headers={"Cache-Control": "no-store"})
    except (KeyError, ValueError) as exc:
        raise error(exc)


@app.post("/incidents/{incident_id}/decision")
def decision(incident_id: str, request: DecisionRequest):
    try:
        with connection() as db:
            return decide(db, incident_id, request.approve, request.expected_sequence)
    except (KeyError, ValueError) as exc:
        raise error(exc)


@app.websocket("/runs/{run_id}/stream")
async def stream(websocket: WebSocket, run_id: str, after: int = 0):
    origin = websocket.headers.get("origin")
    allowed = set(os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","))
    if origin and origin not in allowed:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    sequence = after
    try:
        while True:
            with connection(write=False) as db:
                new = events(db, run_id, after=sequence)
            for item in new:
                await websocket.send_json(item)
                sequence = item["sequence_number"]
            await asyncio.sleep(0.6)
    except WebSocketDisconnect:
        pass
