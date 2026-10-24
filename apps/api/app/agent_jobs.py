"""Durable agent job queue with short claims, fencing, retries and budgets.

Only PostgreSQL is supported for concurrent production workers. SQLite fixtures
are supported for sequential tests. All model/network work happens after COMMIT.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .agent_workflow import MODEL_RESERVATIONS, PHASES, advance
from .live_models import AgentJob, AuditRecord, LiveIncident, LiveInvestigation, Workspace, engine, session_factory

LEASE_SECONDS = 180
RETRY_ATTEMPTS = 3
DEFAULT_DEADLINE_SECONDS = 900
TERMINAL = {"succeeded", "cancelled", "failed"}


def utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _deadline() -> datetime:
    duration = max(60, min(3600, int(os.getenv("AGENT_JOB_DEADLINE_SECONDS", "900"))))
    return utc() + timedelta(seconds=duration)


def _reservation_price() -> int:
    price = int(os.getenv("AGENT_COST_RESERVATION_MICROUSD_PER_CALL", "20000"))
    if not 1 <= price <= 1000000:
        raise ValueError("Invalid configured estimated cost per model call")
    return price


def serialize(job: AgentJob) -> dict:
    return {"id": job.id, "workspace_id": job.workspace_id, "incident_id": job.incident_id,
            "status": job.status, "phase": job.phase, "attempt": job.attempt,
            "max_attempts": job.max_attempts, "cancel_requested": job.cancel_requested,
            "deadline_at": utc(job.deadline_at).isoformat(),
            "model_call_slots_reserved": job.reserved_model_calls,
            "max_model_calls": job.max_model_calls,
            "estimated_cost_microusd": job.estimated_cost_microusd,
            "max_cost_microusd": job.max_cost_microusd,
            "investigation_id": job.investigation_id, "last_error": job.last_error,
            "updated_at": utc(job.updated_at).isoformat(),
            "progress": {"completed_plans": len((job.checkpoint or {}).get("plans", [])),
                         "evidence_reads": len((job.checkpoint or {}).get("visited", [])),
                         "specialist": (job.checkpoint or {}).get("specialist"),
                         "evidence_gaps": (job.checkpoint or {}).get("evidence_gaps", [])},
            "cost_note": "Cost is a reserved estimate per possible model call, not billed provider usage."}


def enqueue(db: Session, incident: LiveIncident, requested_by_id: str,
            key: str, *, max_calls: int = 7, max_cost_microusd: int = 200000) -> AgentJob:
    if not 1 <= len(key) <= 160:
        raise ValueError("Invalid job idempotency key")
    if not 1 <= max_calls <= 20 or not 10000 <= max_cost_microusd <= 10000000:
        raise ValueError("Invalid model or cost budget")
    existing = db.scalar(select(AgentJob).where(
        AgentJob.workspace_id == incident.workspace_id,
        AgentJob.idempotency_key == key))
    if existing is not None:
        if existing.incident_id != incident.id:
            raise ValueError("Job idempotency key belongs to another incident")
        return existing
    job = AgentJob(id=str(uuid4()), workspace_id=incident.workspace_id,
                   incident_id=incident.id, requested_by_id=requested_by_id,
                   idempotency_key=key, status="queued", phase="plan", checkpoint={},
                   attempt=0, max_attempts=RETRY_ATTEMPTS, cancel_requested=False,
                   deadline_at=_deadline(), max_model_calls=max_calls,
                   reserved_model_calls=0, max_cost_microusd=max_cost_microusd,
                   estimated_cost_microusd=0, reserved_cost_microusd=0,
                   created_at=utc(), updated_at=utc())
    db.add(job)
    db.flush()
    return job


def request_cancel(db: Session, job: AgentJob) -> None:
    if job.status in TERMINAL:
        return
    job.cancel_requested = True
    if job.status == "queued":
        job.status = "cancelled"
    job.updated_at = utc()


def resume(db: Session, job: AgentJob) -> None:
    if job.status not in {"cancelled", "failed"}:
        raise ValueError("Only cancelled or failed agent jobs can be resumed")
    if job.investigation_id:
        raise ValueError("Completed investigation cannot be resumed")
    job.cancel_requested = False
    job.status = "queued"
    job.attempt = 0
    job.locked_by = None
    job.lease_until = None
    job.next_attempt_at = None
    job.last_error = None
    job.deadline_at = _deadline()
    job.updated_at = utc()


def _claim(worker_id: str) -> tuple[str, str, str, dict] | None:
    with session_factory()() as db:
        now = utc()
        available = or_(AgentJob.status == "queued",
                        and_(AgentJob.status == "running", AgentJob.lease_until < now))
        stmt = select(AgentJob).where(
            available,
            or_(AgentJob.next_attempt_at.is_(None), AgentJob.next_attempt_at <= now),
        ).order_by(AgentJob.created_at, AgentJob.id).limit(1)
        if engine().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        job = db.scalar(stmt)
        if job is None:
            return None
        if job.cancel_requested or utc(job.deadline_at) <= now:
            job.status = "cancelled" if job.cancel_requested else "failed"
            job.last_error = "Cancelled" if job.cancel_requested else "Job deadline expired"
            job.locked_by = None
            job.lease_until = None
            db.commit()
            return None
        token = str(uuid4())
        job.status = "running"
        job.locked_by = token
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        job.updated_at = now
        db.commit()
        return job.id, token, job.phase, dict(job.checkpoint or {})


def _reserve(job_id: str, token: str, phase: str) -> bool:
    """Durably reserve the phase's worst-case model call slots before inference."""
    with session_factory()() as db:
        query = select(AgentJob).where(AgentJob.id == job_id)
        if engine().dialect.name == "postgresql":
            query = query.with_for_update()
        job = db.scalar(query)
        if (job is None or job.locked_by != token or job.status != "running"
                or job.lease_until is None or utc(job.lease_until) <= utc()):
            return False
        if job.cancel_requested or utc(job.deadline_at) <= utc():
            job.status = "cancelled" if job.cancel_requested else "failed"
            job.locked_by = None
            job.lease_until = None
            db.commit()
            return False
        count = MODEL_RESERVATIONS[phase]
        reserve = count * _reservation_price()
        if (job.reserved_model_calls + count > job.max_model_calls or
                job.reserved_cost_microusd + reserve > job.max_cost_microusd):
            job.status = "failed"
            job.last_error = "Model-call or estimated cost reservation exhausted"
            job.locked_by = None
            job.lease_until = None
            db.commit()
            return False
        job.reserved_model_calls += count
        job.reserved_cost_microusd += reserve
        job.estimated_cost_microusd = job.reserved_cost_microusd
        db.commit()
        return True


def _finish(job_id: str, token: str, phase: str, checkpoint: dict) -> bool:
    """Fenced transaction: the old owner cannot overwrite a resumed/claimed job."""
    with session_factory()() as db:
        query = select(AgentJob).where(AgentJob.id == job_id)
        if engine().dialect.name == "postgresql":
            query = query.with_for_update()
        job = db.scalar(query)
        if (job is None or job.locked_by != token or job.status != "running"
                or job.lease_until is None or utc(job.lease_until) <= utc()):
            return False
        if job.cancel_requested or utc(job.deadline_at) <= utc():
            job.status = "cancelled" if job.cancel_requested else "failed"
            job.last_error = "Cancelled" if job.cancel_requested else "Job deadline expired"
        elif phase == "finalize":
            analysis = checkpoint.get("analysis")
            if not analysis:
                raise ValueError("Cannot finalize an unfinished investigation")
            critic = checkpoint.get("critic") or {}
            trace = list(checkpoint.get("trace", [])) + list(analysis.get("tool_trace", []))
            if critic.get("status") != "passed":
                analysis = {**analysis, "mode": "deterministic_fallback",
                            "finding": analysis["finding"].split(" AI interpretation for human review:", 1)[0]}
                trace = [{**event, "proposals": []} if event.get("tool") == "policy_check"
                         else event for event in trace]
            record = LiveInvestigation(workspace_id=job.workspace_id,
                                      incident_id=job.incident_id,
                                      mode=analysis["mode"], finding=analysis["finding"],
                                      evidence=analysis["evidence"], tool_trace=trace)
            db.add(record)
            db.flush()
            job.investigation_id = record.id
            workspace = db.get(Workspace, job.workspace_id)
            if workspace is not None:
                db.add(AuditRecord(workspace_id=job.workspace_id, user_id=job.requested_by_id,
                                   action="agentic_job_completed", target_id=job.id,
                                   detail={"investigation_id": record.id, "mode": record.mode,
                                           "critic_verified": critic.get("status") == "passed"}))
            job.status = "succeeded"
            job.phase = "done"
            job.checkpoint = {"completed": True, "specialist": checkpoint.get("specialist"),
                              "critic": critic, "plans": checkpoint.get("plans", []),
                              "evidence_gaps": checkpoint.get("evidence_gaps", []),
                              "visited": checkpoint.get("visited", [])}
        else:
            job.phase = checkpoint.get("phase", phase)
            job.checkpoint = checkpoint
            job.status = "queued"
            job.attempt = 0
            job.next_attempt_at = None
        job.locked_by = None
        job.lease_until = None
        job.updated_at = utc()
        db.commit()
        return True


def _fail(job_id: str, token: str, error: Exception) -> None:
    with session_factory()() as db:
        query = select(AgentJob).where(AgentJob.id == job_id)
        if engine().dialect.name == "postgresql":
            query = query.with_for_update()
        job = db.scalar(query)
        if (job is None or job.locked_by != token or job.status != "running"
                or job.lease_until is None or utc(job.lease_until) <= utc()):
            return
        job.attempt += 1
        job.last_error = type(error).__name__  # Never persist model prompts, secrets, or PII.
        if job.cancel_requested:
            job.status = "cancelled"
        elif job.attempt >= job.max_attempts or utc(job.deadline_at) <= utc():
            job.status = "failed"
        else:
            job.status = "queued"
            job.next_attempt_at = utc() + timedelta(seconds=min(120, 2 ** job.attempt * 5))
        job.locked_by = None
        job.lease_until = None
        job.updated_at = utc()
        db.commit()


def run_one_stage(worker_id: str = "worker") -> tuple[str, str] | None:
    claim = _claim(worker_id)
    if claim is None:
        return None
    job_id, token, phase, checkpoint = claim
    if phase not in PHASES:
        _fail(job_id, token, ValueError("Unexpected phase"))
        return job_id, "failed"
    if not _reserve(job_id, token, phase):
        return job_id, "stopped"
    try:
        if phase == "finalize":
            next_state = checkpoint
        else:
            with session_factory()() as db:
                job = db.get(AgentJob, job_id)
                if job is None:
                    raise ValueError("Job disappeared")
                incident = db.get(LiveIncident, job.incident_id)
                if incident is None or incident.workspace_id != job.workspace_id:
                    raise ValueError("Job incident is unavailable in its workspace")
                next_state = advance(db, incident, phase, checkpoint)
        saved = _finish(job_id, token, phase, next_state)
        return job_id, "succeeded" if saved and phase == "finalize" else ("advanced" if saved else "stale")
    except Exception as exc:
        _fail(job_id, token, exc)
        return job_id, "retry"


def auto_key(incident: LiveIncident) -> str:
    evidence = json.dumps(incident.evidence or [], sort_keys=True)
    digest = hashlib.sha256((incident.fingerprint + "|" + evidence).encode()).hexdigest()[:16]
    return "auto:" + incident.id + ":" + digest
