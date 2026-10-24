# OpsVerse durable + adaptive agents (experimental)

This change adds **Phase 1 durable agent execution** and **Phase 2 adaptive evidence investigation** on top of `agentic-investigations-v1`. It does **not** make unrestricted public SaaS production-ready and has not been validated against a live model or merchant account.

## Phase 1: crash-safe job state

- Alembic migration `e8d93ef271a1` adds `agent_jobs`, scoped by workspace and incident, with workspace-specific idempotency keys.
- Job phases: `plan -> evidence -> [replan -> evidence] -> analyze -> critic -> finalize`. Each phase persists a checkpoint. A job can be paused indirectly by stopping the worker, then resumed on restart.
- Workers claim `queued` jobs using PostgreSQL `FOR UPDATE SKIP LOCKED`; short database transactions claim, budget-reserve and commit phases. No transaction is held through a model call.
- 180-second leases and fencing tokens prevent stale owners from overwriting jobs. Expired claims are recoverable. Retryable exceptions back off (maximum three attempts per phase); failed and cancelled jobs can be explicitly resumed.
- Jobs have an absolute deadline (default 900 seconds) and per-run maximum LLM completion reservation (default 7).
- **Cost is an estimated reservation**, not live provider billing. Default worst-case reservation US$0.02 per possible LLM completion, US$0.20 per job. Settings below. Limits are enforced before each model stage. Token-level bill accuracy and strict provider spending limits still require account-side quotas.
- Cancellation is cooperative at phase boundaries, not an immediate kill of an in-flight provider call. The model HTTP request has a 10-second per-call timeout and the analyst has a four-call cap; phase completion checks the deadline and lease again.
- `finalize` atomically commits the `LiveInvestigation`, audit record and job completion; therefore retrying finalization cannot create duplicate reports.
- This is a database-backed job queue, **not** a deployment of Temporal. For very high concurrency or hours-long jobs, add worker heartbeat, monitoring and stronger cancellation semantics.

## Phase 2: adaptive evidence workflow

- `agent_specialists.py` selects inventory, payments, provenance, or general tool families.
- The LLM planner generates 1-3 *competing, unverified* hypotheses and up to 4 bounded read queries using existing schema-validated workspace-scoped tool functions.
- Tool outcomes are saved into the checkpoint. Missing, failed, or insufficiently discriminating evidence may trigger **one** bounded replan. Previously attempted query signatures are tracked so repeated queries are skipped.
- Source-backed candidate incident correlation uses exact overlapping reference IDs/external IDs, scoped to one workspace and labeled **possible overlap, not causality**.
- The existing evidence-validated analyst receives collected evidence and performs deterministic quantitative checks. A separate skeptical critic validates cited source IDs. Missing or contradicted evidence causes fallback, not a false claim that an issue was fixed.
- No real merchant refund, payment, inventory change, message, or supplier order is executed.

## Local setup (Windows PowerShell)

Review and save uncommitted files first:

```powershell
cd C:\Users\91921\OPSVERSE
git status --short
git fetch origin
git switch durable-adaptive-agent-v1
# If the previous command says branch not found, use:
# git switch --track origin/durable-adaptive-agent-v1
```

Edit your local, untracked `.env` using `notepad .env`:

```dotenv
LLM_BASE_URL=https://YOUR_OPENAI_COMPATIBLE_PROVIDER/v1
LLM_MODEL=YOUR_VERIFIED_TOOL_CAPABLE_MODEL
LLM_API_KEY=YOUR_SECRET
AGENT_AUTORUN_ENABLED=false
AGENT_AUTO_WORKSPACE_IDS=
AGENT_DAILY_RUNS_PER_WORKSPACE=20
AGENT_JOB_DEADLINE_SECONDS=900
AGENT_COST_RESERVATION_MICROUSD_PER_CALL=20000
```

```powershell
docker compose up -d --build db api sync web agent
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health
Start-Process http://127.0.0.1:3000/live
```

Open **Live > Incidents > Queue durable AI investigation**, then **Agent Traces > Checkpointed investigations** to review status, budget and cancel/resume buttons. The worker picks up the queued job even with `AGENT_AUTORUN_ENABLED=false` (that flag controls proactive scanning, not processing a manually requested job).

For autonomous scanning, explicitly set **both** `AGENT_AUTORUN_ENABLED=true` and `AGENT_AUTO_WORKSPACE_IDS=<consenting workspace UUID>`, then recreate `api` and `agent` using `docker compose up -d --force-recreate api agent`.

## HTTP contracts

- `POST /live/workspaces/{workspace_id}/agent/jobs` with `{"incident_id": "<UUID>", "idempotency_key": "<optional stable client key>", "max_model_calls": 7, "max_cost_microusd": 200000}`: enqueues a job and returns 202. Repeating the same idempotency key returns the same job.
- `GET /live/workspaces/{workspace_id}/agent/jobs`: 100 most recent workspace jobs.
- `GET /live/workspaces/{workspace_id}/agent/jobs/{job_id}`: status/progress.
- `POST /live/workspaces/{workspace_id}/agent/jobs/{job_id}/cancel`: requests cancellation.
- `POST /live/workspaces/{workspace_id}/agent/jobs/{job_id}/resume`: explicit restart of a cancelled or failed job.
- `GET /live/workspaces/{workspace_id}/incidents/{incident_id}/related`: candidate overlapping incidents.

All endpoints use authenticated workspace membership; write routes require analyst/owner role and same-origin protection. Never expose the database or agent worker ports to the internet.

## Tests and rollout gates

Run on a disposable test database:

```powershell
python -m pytest apps/api/tests -q
pnpm --dir apps/web build
```

CI runs the appropriate jobs for the code branch; passing tests verify stubbed execution, not external model reasoning quality.

Before production deployment, verify lock fencing under two real concurrent PostgreSQL workers, restart during every model phase, cancellation while blocked, a genuine 429/503 provider response, expired lease recovery, tenant isolation, and provider pricing/usage estimates. Also exercise the existing private-pilot HTTPS configuration, off-host backups and restoration, and confirmed-account Shopify/Razorpay flows.

**Still not implemented:** arbitrary autonomous actions, user-approved external write operations, general anomaly discovery beyond the existing detectors, comprehensive business-wide simulation, independent LLM evaluation dataset, or billing-quality cost accounting.
