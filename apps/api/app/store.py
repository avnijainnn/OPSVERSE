"""Transactional SQLite event store; PostgreSQL adapter can replace this boundary."""
from __future__ import annotations

import json
import os
import sqlite3
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .domain import apply, project

DB_PATH = Path(os.getenv("OPSVERSE_DB", str(Path(__file__).resolve().parents[2] / "opsverse.db")))
EPOCH = datetime(2026, 10, 15, 3, 30, tzinfo=timezone.utc)  # 09:00 Asia/Kolkata
_projection_cache: dict[tuple[str, str], tuple[int, str | None, dict[str, Any]]] = {}


@contextmanager
def connection(*, write: bool = True) -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    if write:
        db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=20) as db:
        db.execute("PRAGMA journal_mode=WAL")
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS simulation_runs (
            run_id TEXT PRIMARY KEY, seed INTEGER NOT NULL, parent_run_id TEXT,
            fork_event_id TEXT, clock TEXT NOT NULL, status TEXT NOT NULL,
            speed INTEGER NOT NULL DEFAULT 1, configuration TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS business_events (
            event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
            sequence_number INTEGER NOT NULL, aggregate_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL, event_type TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1, timestamp TEXT NOT NULL,
            recorded_at TEXT NOT NULL, payload TEXT NOT NULL,
            causation_id TEXT, correlation_id TEXT, idempotency_key TEXT,
            UNIQUE(run_id, sequence_number), UNIQUE(run_id, idempotency_key));
        CREATE INDEX IF NOT EXISTS ix_events_run_type ON business_events(run_id,event_type);
        CREATE TABLE IF NOT EXISTS incidents (
            incident_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES simulation_runs(run_id),
            type TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL,
            trigger_event_id TEXT NOT NULL, evidence_event_ids TEXT NOT NULL,
            affected_entities TEXT NOT NULL, description TEXT NOT NULL,
            root_cause TEXT NOT NULL, resolution TEXT,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_executions (
            execution_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
            agent_name TEXT NOT NULL, mode TEXT NOT NULL, tools TEXT NOT NULL,
            finding TEXT NOT NULL, evidence_event_ids TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_tool_calls (
            call_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES agent_executions(execution_id),
            tool_name TEXT NOT NULL, arguments TEXT NOT NULL, result TEXT NOT NULL,
            duration_ms REAL NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recovery_plans (
            plan_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
            action_type TEXT NOT NULL, preconditions TEXT NOT NULL,
            risk_level TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS scenario_results (
            result_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
            baseline_run_id TEXT NOT NULL, recovery_run_id TEXT NOT NULL,
            baseline_metrics TEXT NOT NULL, recovery_metrics TEXT NOT NULL,
            created_at TEXT NOT NULL, third_run_id TEXT, third_metrics TEXT);
        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
            decision TEXT NOT NULL, expected_sequence INTEGER NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS verification_results (
            verification_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
            passed INTEGER NOT NULL, checks TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        # executescript commits the outer transaction; re-lock before checking
        # columns so concurrent TestClient startups cannot race this migration.
        db.execute("BEGIN IMMEDIATE")
        existing = {row["name"] for row in db.execute("PRAGMA table_info(scenario_results)")}
        if "third_run_id" not in existing:
            db.execute("ALTER TABLE scenario_results ADD COLUMN third_run_id TEXT")
        if "third_metrics" not in existing:
            db.execute("ALTER TABLE scenario_results ADD COLUMN third_metrics TEXT")


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("payload", "configuration", "evidence_event_ids", "affected_entities", "tools", "preconditions", "baseline_metrics", "recovery_metrics", "third_metrics", "checks", "arguments", "result"):
        if key in out and out[key] is not None:
            out[key] = json.loads(out[key])
    return out


def rows(db: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    return [_decode(row) for row in db.execute(sql, args).fetchall()]


def run(db: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    found = rows(db, "SELECT * FROM simulation_runs WHERE run_id=?", (run_id,))
    if not found:
        raise KeyError("Unknown run")
    return found[0]


def events(db: sqlite3.Connection, run_id: str, after: int = 0, until: int | None = None) -> list[dict[str, Any]]:
    if until is None:
        return rows(db, "SELECT * FROM business_events WHERE run_id=? AND sequence_number>? ORDER BY sequence_number", (run_id, after))
    return rows(db, "SELECT * FROM business_events WHERE run_id=? AND sequence_number>? AND sequence_number<=? ORDER BY sequence_number", (run_id, after, until))


def append(db: sqlite3.Connection, run_id: str, kind: str, payload: dict[str, Any],
           aggregate_type: str, aggregate_id: str, causation_id: str | None = None,
           idempotency_key: str | None = None, advance_minutes: int = 3,
           timestamp_override: str | None = None) -> dict[str, Any]:
    if idempotency_key:
        existing = rows(db, "SELECT * FROM business_events WHERE run_id=? AND idempotency_key=?", (run_id, idempotency_key))
        if existing:
            return existing[0]
    tip = db.execute("SELECT sequence_number,event_id FROM business_events WHERE run_id=? ORDER BY sequence_number DESC LIMIT 1", (run_id,)).fetchone()
    last_sequence = tip["sequence_number"] if tip else 0
    last_id = tip["event_id"] if tip else None
    sequence = last_sequence + 1
    cache_key = (str(DB_PATH), run_id)
    cached = _projection_cache.get(cache_key)
    state = deepcopy(cached[2]) if cached and cached[0] == last_sequence and cached[1] == last_id else project(events(db, run_id))
    current_clock = db.execute("SELECT clock FROM simulation_runs WHERE run_id=?", (run_id,)).fetchone()["clock"]
    timestamp = timestamp_override or (datetime.fromisoformat(current_clock) + timedelta(minutes=advance_minutes)).isoformat()
    envelope = {"event_id": str(uuid4()), "run_id": run_id, "sequence_number": sequence,
                "aggregate_type": aggregate_type, "aggregate_id": aggregate_id,
                "event_type": kind, "schema_version": 1, "timestamp": timestamp,
                "recorded_at": datetime.now(timezone.utc).isoformat(), "payload": payload,
                "causation_id": causation_id, "correlation_id": aggregate_id,
                "idempotency_key": idempotency_key}
    next_state = apply(state, envelope)
    db.execute("""INSERT INTO business_events VALUES
        (:event_id,:run_id,:sequence_number,:aggregate_type,:aggregate_id,:event_type,
         :schema_version,:timestamp,:recorded_at,:payload,:causation_id,:correlation_id,:idempotency_key)""",
        {**envelope, "payload": json.dumps(payload, sort_keys=True)})
    db.execute("UPDATE simulation_runs SET clock=? WHERE run_id=?", (timestamp, run_id))
    _projection_cache[cache_key] = (sequence, envelope["event_id"], next_state)
    return envelope


def create_run(db: sqlite3.Connection, seed: int, parent: str | None = None,
               fork_sequence: int | None = None) -> str:
    run_id = str(uuid4())
    parent_events = events(db, parent, until=fork_sequence) if parent else []
    fork_event_id = parent_events[-1]["event_id"] if parent_events else None
    db.execute("INSERT INTO simulation_runs VALUES (?,?,?,?,?,?,?,?,?)",
               (run_id, seed, parent, fork_event_id, EPOCH.isoformat(), "paused", 1,
                json.dumps({"world": "Aaranya Commerce"}), datetime.now(timezone.utc).isoformat()))
    copied_ids: dict[str, str] = {}
    for event in parent_events:
        payload = deepcopy(event["payload"])
        if "source_event_id" in payload:
            payload["source_event_id"] = copied_ids.get(payload["source_event_id"], payload["source_event_id"])
        copied = append(db, run_id, event["event_type"], payload, event["aggregate_type"],
               event["aggregate_id"], copied_ids.get(event["causation_id"], event["causation_id"]),
               event["idempotency_key"], timestamp_override=event["timestamp"])
        copied_ids[event["event_id"]] = copied["event_id"]
    return run_id
