# OpsVerse V2 implementation gap audit

Audit performed against the repository on 2026-10-17 before V2 code changes. This is a code and local-fixture audit; no merchant, Shopify, Razorpay, or LLM credentials were available.

## Existing components verified in code

| Area | Existing implementation | V2 gap |
| --- | --- | --- |
| Persistence and isolation | SQLAlchemy workspace-owned records in `live_models.py`, Alembic migrations through `d32f0a74c85e`, membership checks in live routes, append-only `Observation` ledger | New routes must apply the same workspace check. No migration is inherently needed for derived views stored in existing JSON fields. |
| Ingestion and detection | CSV imports, Shopify/Razorpay adapters, source observations, deterministic incident rules in `live_detection.py` | Provider behavior cannot be externally verified without credentials. |
| Incident investigation | Source-linked incidents, source drawer endpoint, bounded optional model loop and deterministic fallback in `live_ai.py` | No dedicated live evidence graph; limited evidence classification, missing-evidence accounting, and structured claim validation. |
| Digital twin | `freeze_state` reconstructs latest observation versions by `ingested_at`; immutable JSON snapshot and digest | Historical validation and forecast-versus-actual evaluation absent. |
| Scenarios | Three deterministic branches sharing a demand schedule and saved replay/verification | Branches are inventory quantity policies, not a broader recovery-policy engine. Limited assumptions, no sensitivity or metric difference trace. |
| Finance | Payment/settlement/bank and Razorpay reconciliation endpoints | Stable reason codes and all-source adjustment tracing incomplete. |
| Frontend | Authenticated live page, isolated SQLite synthetic demo, React Flow causal graph for synthetic events, Playwright journeys | Live incident graph, backtesting, receipt, and guided V2 navigation absent. |

## Existing model and route reuse

Use `LiveIncident.evidence` and `Observation` for a derived evidence graph; `LiveSnapshot.source_state` and `LiveScenario.branches/disruption` for simulation extensions; `AuditRecord` for review/verification history. Extend authenticated `/live/workspaces/{workspace_id}` routes. Keep `/demo` SQLite synthetic data separate from PostgreSQL live records. Use the already installed `@xyflow/react` package.

## Risks and implementation decisions

- Observation history begins at ingestion. Backtesting must reject cutoffs without enough *ingested* history and must never backfill knowledge using a later import's older business timestamp.
- The existing branch engine uses the same demand schedule but only models stock fulfillment. New outputs must be described as hypothetical estimates with explicit modeled assumptions.
- Financial matching must follow provider identifiers, never amount-only joins. Unsupported FX and chargeback causes must remain unresolved.
- Model integration is optional; local fixture tests can verify validation and fallback, but cannot establish real model quality or provider behavior.
- Preserve existing routes and UI labels used in regression journeys while adding the V2 workspace flow.

## Baseline verification

The pre-change Next.js production build passed. The first API attempt reached 23 SQLite tests, then the PostgreSQL fixture failed to connect because WSL localhost forwarding stopped while the distro was idle. A direct PostgreSQL connection succeeded after restarting WSL and keeping a foreground WSL process active; the four PostgreSQL runtime tests then passed. This was an environment connectivity failure, not an application assertion failure. Existing pre-V2 browser captures were present under `docs/screenshots`; the new journey is being re-exercised against a real FastAPI/PostgreSQL server.

After Stage 1, the focused incident graph test passed. After Stages 2–6, 12 focused live/backtest tests passed. The complete API suite and new Playwright journey are rerunning after the isolated demo extension; final counts belong in `VALIDATION_AND_LIMITATIONS.md` once they complete.

## Planned changes in required order

1. Workspace-scoped incident graph and interactive source navigation.
2. More expressive isolated branch calculations, assumption controls, sensitivity, event attribution.
3. Ingestion-cutoff rolling backtests with simple prespecified baselines.
4. Structured investigation output and stricter source/amount validation using the bounded read-only loop.
5. Exact two-stage money reconciliation with stable, evidence-backed reason codes.
6. Printable Decision Receipt and reproducibility manifest.
7. Connected Command Center → Incident Microscope → Decision Lab flow and guided local demo.
8. PostgreSQL, browser, security, performance, documentation, and screenshot regression checks.

## Implemented V2 scope and remaining gaps

- Stage 1: The authenticated Incident Microscope has a React Flow graph, observation timeline, coverage and missing-version messages, source navigation, and a Decision Lab link. It derives only explicit or labeled inferred relationships.
- Stage 2: Saved three-branch scenarios accept bounded restock, delay and cost inputs. Seed sweeps report mean and range; an explorer joins matched demand IDs. The model remains a simplified inventory simulator, not a general commerce policy engine.
- Stage 3: A rolling order-volume backtest uses business event and ingestion time and preselected last-value/three-window baselines. It returns insufficient history when observations do not cover the window. Returns, inventory, settlement timing and recovery estimates are not yet validated against actual outcomes.
- Stage 4: The optional model loop offers typed workspace-scoped read tools and validates cited IDs and figures. No external model endpoint was available; only fixture/model-contract tests ran.
- Stage 5: Money Trail exposes exact recorded adjustments, two-stage variances, stable supported reason codes and source paths. It does not infer chargebacks, FX differences, duplicate causes or fee variance without source evidence.
- Stage 6: Private printable HTML and JSON receipts contain source IDs, branch manifest, review and verification; browser print supports PDF. No server-side PDF generator was added.
- Stage 7: The live UI has a three-step Command Center → Incident Microscope → Decision Lab rail. The isolated SQLite demo has three branch ledgers, a bounded delay sweep, printable receipt and guided replay. The demo's third branch is a deterministic delayed review, not an AI-generated policy.
- Stage 8: Regression and UI checks are ongoing. Real provider connection, model inference, hosted infrastructure and merchant-population accuracy remain unverified.
