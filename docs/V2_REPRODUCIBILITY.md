# V2 reproducibility and verification

## Local startup

From the repository root, run `docker compose up --build` and open `http://localhost:3000`. The API is at `http://localhost:8000`. Alternatively, set `DATABASE_URL` and `SESSION_SECRET`, run `python -m alembic upgrade head` from `apps/api`, start `uvicorn app.main:app`, and start the frontend with `pnpm --dir apps/web dev`. Live Operations requires PostgreSQL for the primary validation path; Demo Mode uses isolated SQLite.

## No-credential guided demo

Open `/demo`, choose **Guided tour**, and follow the steps. It creates a seed-2026 synthetic business, injects a damaged return, opens the evidence graph, runs three isolated branch ledgers, sweeps manual-review delay, verifies a sandbox action, opens a printable receipt, and replays the event chain. Use **Launch Demo** to reset to a new run. The event ledger is synthetic and never enters a live workspace.

## Authenticated fixture journey

Create a workspace at `/live`. Import `products` and `inventory` CSVs using the format in [CSV formats](CSV_FORMATS.md), with two inventory rows for the same SKU, location and observation time but different sellable counts. The detection queue opens an inventory mismatch. Open the incident to inspect the graph and observation versions, run the deterministic investigator, then select **Compare Recovery Strategies**. Create an observed snapshot, run three futures, change restock units, rerun, inspect sensitivity and event differences, review a plan, verify sandbox replay, and open the Decision Receipt. A source record is never mutated by these actions.

For a historical backtest, order observations must span three prior complete horizon windows and a later observed window. A fresh CSV import, even when its business timestamps are old, cannot provide knowledge at an earlier ingestion cutoff. The UI correctly reports insufficient history in that case. The pure synthetic test in `apps/api/tests/test_backtest.py` exercises rolling cutoffs and late-import leakage prevention.

## Commands

```bash
python -m pytest apps/api/tests -q
pnpm --filter @opsverse/web lint
pnpm --filter @opsverse/web build
pnpm --filter @opsverse/web exec playwright test
python scripts/benchmark.py --seed 2026 --output reports/benchmark.json
```

Set `POSTGRES_TEST_URL` to a disposable PostgreSQL database before the API suite to include PostgreSQL runtime tests. Set `DATABASE_URL` and `SESSION_SECRET` to that database for the Playwright live journey. The browser test uses real FastAPI requests and CSV fixture imports. Benchmark sample counts and external verification status are in [validation and limitations](VALIDATION_AND_LIMITATIONS.md).

## Service routes added in V2

- `GET /live/workspaces/{workspace_id}/incidents/{incident_id}/evidence-graph`
- `POST /live/workspaces/{workspace_id}/backtests`
- `GET /live/workspaces/{workspace_id}/scenarios/{scenario_id}/difference`
- `POST /live/workspaces/{workspace_id}/scenarios/{scenario_id}/sensitivity`
- `GET /live/workspaces/{workspace_id}/scenarios/{scenario_id}/receipt.json`
- `GET /live/workspaces/{workspace_id}/scenarios/{scenario_id}/receipt`
- `POST /incidents/{incident_id}/sensitivity` (synthetic demo only)
- `GET /incidents/{incident_id}/receipt` (synthetic demo only)

V2 uses existing PostgreSQL JSON and audit tables, so it adds no PostgreSQL Alembic revision. The SQLite demo bootstraps two nullable columns on its scenario table for a third run and its metrics.
