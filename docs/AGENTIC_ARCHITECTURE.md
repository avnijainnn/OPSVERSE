# OpsVerse agent-driven investigation (experimental v1)

## What changed

The authenticated Live workspace now exposes **Run AI agent workflow** in an incident and `POST /live/workspaces/{workspace_id}/incidents/{incident_id}/investigate/agentic`.

**Planner** proposes 1–3 tentative hypotheses and up to four read-only evidence queries. **Evidence executor** invokes only allowlisted workspace-scoped tools with validated arguments. **Analyst** uses the existing bounded model function-calling loop (maximum four completions and eight tool calls) and deterministic arithmetic checks. **Skeptical critic** independently assesses the grounded conclusion and can mark it supported, uncertain or rejected. If the model is unavailable, planner fails, analyst fails validation, or critic rejects the result, the finding falls back to the deterministic result and AI-selected policies are withheld.

`GET /live/workspaces/{workspace_id}/agent/status` shows whether a model is configured, whether opt-in auto-investigation is enabled, and the stage names. **Agent Traces** stores planner/analyst/critic outcomes and tool IDs in a workspace-owned investigation. Imported source records and provider systems remain unchanged. No unapproved action executes.

## Required model setup

Put server-only values in your **untracked** `.env` file:

```dotenv
LLM_BASE_URL=https://YOUR_OPENAI_COMPATIBLE_PROVIDER/v1
LLM_MODEL=YOUR_VERIFIED_TOOL_CAPABLE_MODEL
LLM_API_KEY=YOUR_SECRET_KEY
AGENT_AUTORUN_ENABLED=false
```

Example local Windows PowerShell commands:

```powershell
cd C:\Users\91921\OPSVERSE
git fetch origin
git switch agentic-investigations-v1
docker compose up -d --build db api sync web
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health
Start-Process http://127.0.0.1:3000/live
```

Open a real, source-backed incident and select **Run AI agent workflow**. Inspect **Agent Traces** for an `agent_planner` entry marked `passed`, a `model_completion`, a `model_validation` marked `passed`, and an `agent_critic` marked `passed`. This confirms the code's model path completed; it is **not** proof the model's conclusion is factually correct in the real world. Provider credentials and actual inference were not supplied for local or CI testing.

## Optional autonomous worker

With a trusted model configured, set `AGENT_AUTORUN_ENABLED=true` and then:

```powershell
docker compose up -d --build agent
docker compose logs -f agent
```

It polls for new **open** incidents that have no prior `LiveInvestigation`, prioritizing old entries and bounded to three per sweep by default. A separate Compose `agent` service avoids moving model credentials to the browser. The default is **off**; enabling it incurs provider inference cost. It records an audit event under the workspace owner. PostgreSQL `FOR UPDATE SKIP LOCKED` serializes claims across worker replicas. Do not run multiple SQLite-backed workers.

For a hardened HTTPS pilot, add `-f deploy/compose.live.yml -f deploy/compose.hardening.yml` to Compose invocations and include `agent` in the services; configure Redis, deployment secrets, DNS, TLS, backups, and a monitored runbook before using merchant information. Test against synthetic workspaces and authenticated sources first.

## Important limitations and next phases

This is **agent-driven, bounded investigation**, not general autonomous business operation. It doesn't independently initiate real refunds, change inventory, place orders or send merchant messages. It cannot generate or execute arbitrarily new simulation engines or uncover every unknown failure mode. Any financial amount/claim must still be supported by source IDs and deterministic checks. An independent LLM critic is an additional *probabilistic* safeguard, not a formal correctness proof.

Before calling the full product agentic or public-ready, add durable asynchronous job state and cancellation, an independent adversarial evaluation corpus, human reviewer workflows for unresolved evidence, multi-provider live integration validation, per-SKU demand accuracy, more cross-domain scenario policies, robust usage billing, provider cost budgets, and incident-level alerting. Keep provider write capabilities out of the runtime until tested approvals, reversibility and audit controls exist.
