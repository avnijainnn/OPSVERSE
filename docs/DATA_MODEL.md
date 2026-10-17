# Data model

## Live PostgreSQL schema

`users`, `workspaces` and `memberships` define ownership. Every operational row has a workspace foreign key. `import_batches` records file hashes, status and row errors. `products`, `inventory_snapshots`, `orders`, `order_items`, `returns`, `payments`, `refunds`, `settlements` and `bank_transactions` store normalized imported data with unique `(workspace_id, source_system, external_id)` keys, an import-batch reference and the original source row. Related records use internal foreign keys. `live_incidents` stores a classification and source references; `live_investigations` stores bounded tool traces and cited findings. `live_snapshots` stores an immutable source-state copy and digest, while `live_scenarios` stores matched counterfactual outcomes. `audit_records` records user-triggered imports, investigations and scenario creation. The Alembic migration is in `apps/api/migrations`.

## Legacy synthetic demo ledger

`simulation_runs` identifies seed, parent, fork event, virtual clock, playback status and speed. `business_events` is append-only, uniquely indexed by `(run_id, sequence_number)` and `(run_id, idempotency_key)`; JSON payloads are schema-versioned. `incidents` stores lifecycle, evidence IDs and resolution. `agent_executions`, `recovery_plans`, `scenario_results`, `approvals` and `verification_results` record the investigation and action trail.

Orders, inventory, payments, returns and settlements are reconstructed from run events by pure reducers. Branches carry copied parent events to the fork sequence and then diverge. A small in-process projection cache speeds appends; the ledger remains authoritative. Integer paise are used for all financial arithmetic. The API exposes replay at a requested sequence so a historical projection can be checked against the current projection.
