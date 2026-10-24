"""Durable autonomous and manually requested AI job executor.

The scheduler is opt-in per workspace. Manually enqueued jobs can run with the
scheduler disabled. Provider writes remain disallowed.
"""
from __future__ import annotations

import logging
import os
import time

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from .agent_jobs import auto_key, enqueue, run_one_stage
from .live_models import AgentJob, LiveIncident, LiveInvestigation, Workspace, session_factory
from .live_rate_limit import check_agent_daily_budget

log = logging.getLogger("opsverse.agent_worker")


def permitted_workspaces() -> set[str]:
    return {value.strip() for value in os.getenv("AGENT_AUTO_WORKSPACE_IDS", "").split(",")
            if value.strip()}


def enabled() -> bool:
    """Used by the automatic scheduler; manual jobs are independent."""
    return (os.getenv("AGENT_AUTORUN_ENABLED", "false").lower() == "true"
            and bool(permitted_workspaces())
            and all(os.getenv(key) for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")))


def enqueue_automatic(limit: int = 3) -> int:
    if not enabled():
        return 0
    created = 0
    with session_factory()() as db:
        incidents = db.scalars(select(LiveIncident).where(
            LiveIncident.workspace_id.in_(permitted_workspaces()),
            LiveIncident.status == "open",
        ).order_by(LiveIncident.created_at, LiveIncident.id).limit(200)).all()
        for incident in incidents:
            # Keep v1's one-time automatic behavior for already reviewed incidents.
            if db.scalar(select(LiveInvestigation.id).where(
                LiveInvestigation.incident_id == incident.id).limit(1)):
                continue
            key = auto_key(incident)
            if db.scalar(select(AgentJob.id).where(
                AgentJob.workspace_id == incident.workspace_id,
                AgentJob.idempotency_key == key)):
                continue
            workspace = db.get(Workspace, incident.workspace_id)
            if workspace is None:
                continue
            try:
                check_agent_daily_budget(workspace.id)
                enqueue(db, incident, workspace.owner_id, key)
                db.commit()
                created += 1
            except IntegrityError:
                db.rollback()
            except Exception:
                db.rollback()
                log.exception("Could not enqueue automatic agent investigation")
                break
            if created >= limit:
                break
    return created


def run_sweep(limit: int = 3) -> int:
    """Execute bounded checkpoint transitions; returns number of finished jobs."""
    if not all(os.getenv(key) for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")):
        return 0
    limit = max(1, min(int(limit), 10))
    enqueue_automatic(limit)
    done = 0
    # At most 8 phases per job including one replan; avoids endless worker loops.
    for _ in range(limit * 8):
        result = run_one_stage()
        if result is None:
            break
        job_id, outcome = result
        if outcome == "succeeded":
            done += 1
        if outcome == "retry":
            log.warning("Agent phase failed for job %s; retry scheduled", job_id)
    return done


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    poll = max(30, min(int(os.getenv("AGENT_AUTO_POLL_SECONDS", "60")), 3600))
    batch = max(1, min(int(os.getenv("AGENT_AUTO_BATCH", "3")), 10))
    while True:
        try:
            completed = run_sweep(batch)
            if completed:
                log.info("Completed %d durable agent jobs", completed)
        except Exception:
            log.exception("Durable agent sweep failed")
        time.sleep(poll)


if __name__ == "__main__":
    main()
