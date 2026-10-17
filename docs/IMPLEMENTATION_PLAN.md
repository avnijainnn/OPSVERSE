# Implementation plan

## Completed vertical slices

1. **Synthetic simulator:** deterministic event reducers, isolated branch comparisons, four incident flows, approvals, verification, replay and the eight-screen Demo Mode are preserved at `/demo`. An opt-in seed script generates 300 reproducible synthetic orders.
2. **Live data foundation:** SQLAlchemy models, an Alembic migration and PostgreSQL Compose service hold accounts, workspace-scoped source records, imports, incidents, investigations, snapshots, scenarios and audit records. The legacy synthetic SQLite ledger remains isolated.
3. **CSV operations:** nine source types have UTF-8 CSV validation, column mapping, preview, duplicate checks, batch history and atomic persistence. Dashboard, Money Trail and incident detection derive from imported records.
4. **Live investigation and scenarios:** workspace-scoped read tools, optional bounded LLM tool calling with evidence validation, a deterministic fallback, policy checked recovery proposals, audited human incident reviews, immutable data snapshots and matched-demand inventory scenarios.
5. **Mode-separated UI:** `/live` starts with sign-in and empty source states; `/demo` is visibly labeled synthetic. Browser and API tests exercise both.

## Remaining work, in dependency order

1. **Production persistence verification:** run the migration and full suite against a real PostgreSQL instance; migrate the demo event store when a unified persistent architecture is needed. Add deployment rollback and backup exercises.
2. **Provider integrations:** implement Shopify's current supported installation flow and read-only GraphQL sync, then verified idempotent webhooks; implement Razorpay read-only payments/refunds/settlements sync with encrypted server-side credentials. Contract tests alone are insufficient to call a connector verified; exercise both with authorized test accounts.
3. **Broader live operations:** import inventory movements and fulfillment events, maintain a complete live source event history, detect more cross-system exceptions, and add exact live replay.
4. **Research-grade evaluation:** add labeled negative cases, precision/recall, root-cause grading and fair rules-only versus AI-assisted comparisons. The 300-order fixture exists as an opt-in script; expand its return and settlement diversity.
5. **Production controls:** rate limiting, invitation-based workspace membership, credential rotation, registration policy, incident review state machine, authorization tests for every route, monitoring and a public HTTPS deployment exercise.

The [gap analysis](LIVE_OPERATIONS_GAP_ANALYSIS.md) records the original correction against the starting code. The README states current limitations; no provider connection or external write is represented as complete.
