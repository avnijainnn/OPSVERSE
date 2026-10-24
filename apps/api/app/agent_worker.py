"""Opt-in autonomous scanner: investigate new, source-backed incidents once.

No provider writes and no approval decisions. Run only with a configured model and
AGENT_AUTORUN_ENABLED=true. PostgreSQL row locks prevent simultaneous claims.
"""
from __future__ import annotations

import logging
import os
import time

from sqlalchemy import exists, select

from .live_agentic import agentic_investigate
from .live_models import AuditRecord, LiveIncident, LiveInvestigation, Workspace, engine, session_factory
from .live_rate_limit import check_analysis_budget, check_agent_daily_budget

log = logging.getLogger("opsverse.agent_worker")


def enabled() -> bool:
    return os.getenv("AGENT_AUTORUN_ENABLED", "false").lower() == "true" and all(
        os.getenv(key) for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
    )


def run_sweep(limit: int = 3) -> int:
    """Take bounded claims, persist trace, and never re-run already investigated IDs."""
    if not enabled():
        return 0
    limit = max(1, min(limit, 10))
    processed = 0
    for _ in range(limit):
        with session_factory()() as db:
            prior = exists().where(LiveInvestigation.incident_id == LiveIncident.id)
            query = (select(LiveIncident)
                     .where(LiveIncident.status == "open", ~prior)
                     .order_by(LiveIncident.created_at, LiveIncident.id)
                     .limit(1))
            if engine().dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            incident = db.scalar(query)
            if incident is None:
                break
            workspace = db.get(Workspace, incident.workspace_id)
            if workspace is None:
                log.warning("Ignoring incident with unavailable workspace")
                break
            try:
                check_analysis_budget(workspace.owner_id, incident.workspace_id, "agentic_auto",
                                      max_calls=6)
                check_agent_daily_budget(incident.workspace_id)
                result = agentic_investigate(db, incident)
                db.add(AuditRecord(workspace_id=incident.workspace_id,
                                   user_id=workspace.owner_id,
                                   action="agentic_investigation_auto",
                                   target_id=incident.id,
                                   detail={"mode": result.mode,
                                           "verified": any(x.get("tool") == "agent_critic" and
                                                           x.get("status") == "passed"
                                                           for x in result.tool_trace)}))
                db.commit()
                processed += 1
                log.info("Investigated incident %s (mode=%s)", incident.id, result.mode)
            except Exception:
                db.rollback()
                log.exception("Autonomous investigation failed; no source writes were made")
                break
    return processed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    poll = max(30, min(int(os.getenv("AGENT_AUTO_POLL_SECONDS", "60")), 3600))
    batch = max(1, min(int(os.getenv("AGENT_AUTO_BATCH", "3")), 10))
    while True:
        if enabled():
            try:
                run_sweep(batch)
            except Exception:
                log.exception("Autonomous investigation sweep failed")
        time.sleep(poll)


if __name__ == "__main__":
    main()
