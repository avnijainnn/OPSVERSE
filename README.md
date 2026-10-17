# OpsVerse

OpsVerse now has two explicit experiences:

- **Live Operations** at `/live` is an authenticated, workspace-scoped view of observed business records. It starts empty. CSV imports and authorized provider reads persist in PostgreSQL; metrics and incidents derive from those records.
- **Demo Mode** at `/demo` is the original deterministic, synthetic commerce simulator. It keeps its own event ledger and cannot write to live source tables.

**Understand what happened. Explore what happens next.** The V2 live journey is Command Center → Incident Microscope → Decision Lab. Incident evidence links to saved observation versions; three isolated branches support bounded assumptions, seed-based sensitivity, event differences, order-volume backtesting when history allows, review, sandbox replay and a printable Decision Receipt. The isolated synthetic demo also has three saved branches, a delay sweep, a guided tour, receipt and replay. See the [V2 audit](docs/IMPLEMENTATION_GAP_AUDIT.md) and [reproducibility guide](docs/V2_REPRODUCIBILITY.md).

Live incident investigations use workspace-scoped read tools. With `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` configured, the server can request a model finding; otherwise it returns a deterministic finding. Source references and proposed recovery option IDs are checked before display. Every proposal remains pending human review and performs no external operation.

The [real-world completion audit](docs/REAL_WORLD_COMPLETION_GAPS.md) records implementation status and verification limits.
The [cofounder walkthrough](docs/COFOUNDER_DEMO.md) and [validation matrix](docs/VALIDATION_AND_LIMITATIONS.md) show the actual demo path and what remains unverified.

![Event-grounded synthetic Live Twin](docs/screenshots/demo-live-twin-desktop.png)

![Source-backed live inventory investigation](docs/screenshots/v2-incident-microscope.png)

![Three-branch scenario from imported inventory](docs/screenshots/v2-decision-lab.png)

## Run locally with PostgreSQL

```bash
docker compose up --build
```

Open **http://localhost:3000**. Register an account and a business workspace, then use **Import data**. The API runs at http://localhost:8000 with OpenAPI docs at `/docs`. Compose starts PostgreSQL, runs the Alembic migration, then starts the API and a read-only provider sync worker. The worker checks for due connections every minute and polls each connection at most once per 15 minutes by default. Demo Mode remains available without a provider account.

The Compose password and signing secret defaults are for local development only. Set `POSTGRES_PASSWORD` and a random `SESSION_SECRET` of at least 32 characters before exposing the service. Public hosting also needs HTTPS, `COOKIE_SECURE=true`, appropriate `CORS_ORIGINS`, controlled registration and network access to PostgreSQL. The current public API should not be treated as production hardened.

For development without Compose, provide a PostgreSQL database and set the variables in [.env.example](.env.example), then run:

```bash
cd apps/api
pip install -e '.[test]'
python -m alembic upgrade head
uvicorn app.main:app --reload
# separate terminal, from repository root
pnpm install
pnpm --dir apps/web dev
```

The live API requires `DATABASE_URL` and `SESSION_SECRET`. Without them, `/live` shows a setup error rather than synthetic business data. The synthetic `/demo` route can still run on its local SQLite ledger.

## Provider connections

Set `CREDENTIAL_ENCRYPTION_KEY` to a Fernet key generated with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep this key in a managed secret store; losing it makes stored provider tokens unreadable. Provider credentials are encrypted in the database and never returned to the browser. The Compose file passes the key through from the environment; it has no provider credential default.

For Shopify, create a standalone/API-only Shopify app, set `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`, and `SHOPIFY_REDIRECT_URI`, and allowlist the exact callback URL (`https://YOUR_API/live/shopify/callback`) in the app. The app requests `read_products`, `read_inventory`, `read_locations`, `read_orders`, and `read_returns`. Set `FRONTEND_URL` for the post-authorization redirect. Configure Shopify webhook subscriptions to `https://YOUR_API/live/shopify/webhooks` for order, product, inventory, refund, return, uninstall, and mandatory privacy topics; the endpoint verifies raw-body HMAC signatures and deduplicates delivery IDs. Public hosting requires HTTPS. An authorized INR store starts an initial read-only sync; **Sync now** repeats it. The Admin GraphQL version is pinned to `2026-10`. Product variants and orders paginate, as do their nested inventory levels, order lines, returns, and return lines; sync fails with an explicit error if a connection exceeds 10,000 records. Standard `read_orders` access covers Shopify's default recent-order window. For older orders, obtain Shopify approval, set `SHOPIFY_REQUEST_ALL_ORDERS=true`, and reconnect stores to grant `read_all_orders`; the setting alone does not grant access. No customer PII is requested. The connection screen reports sync errors and last successful sync. Shopify app installation and webhook registration must be configured in Shopify; OpsVerse does not create those external subscriptions.

For Razorpay, set the encryption key, then enter merchant API key ID and secret in Integrations. OpsVerse verifies them with a read-only payments fetch before marking the connection as authenticated. **Sync now** reads captured INR payments, refunds, settlements and settlement reconciliation lines through GET endpoints. Fees and tax stay in integer paise; captured payments with missing fee or tax and non-INR transactions are skipped with a reported limitation. Import a bank statement CSV with `settlement_external_id` equal to the Razorpay settlement ID to match the bank credit. Incremental reads use a five-minute overlapping polling window. Compose runs read-only scheduled polling through its `sync` service. For a non-Compose deployment, schedule `python scripts/sync_due_providers.py --provider razorpay` every minute from an external scheduler; due connections are polled at most once per 15 minutes by default. Set `PROVIDER_SYNC_INTERVAL_MINUTES` and `PROVIDER_WORKER_POLL_SECONDS` to change the Compose cadence. Historical payment, refund, and settlement reads split dense time windows to stay below 10,000 records per window and report an error if a single second exceeds that cap. Reconciliation processes up to 31 settlement days per run and persists remaining days for later runs. Provider retention, API behavior, and completeness still require testing with a merchant account.

To receive Razorpay events, connect the account, then enter a separate webhook secret in **Integrations → Receive signed updates**. Register the displayed API URL and the same secret in the Razorpay dashboard for `payment.captured`, `refund.processed`, and `settlement.processed`. Use a public HTTPS API URL; OpsVerse does not register the subscription in Razorpay. The receiver verifies the raw-body HMAC signature, deduplicates deliveries, stores only event metadata, and performs a read-only provider fetch with retry; polling remains the recovery path. A previous secret is accepted for seven days after rotation so delayed deliveries can complete. This receiver passed signed local fixtures but has not received a real Razorpay delivery.

## Import workflow

Import one UTF-8 CSV at a time. The UI suggests a field-to-column mapping, previews source rows, reports invalid rows and duplicates, and commits an accepted batch atomically. A rejected batch appears in import history and adds no operational records. Start with **products** and **orders**, then import **order items**, **inventory**, **returns**, **payments**, **refunds**, **settlements**, and **bank transactions** as available. Foreign references must already exist in the same workspace.

See [CSV formats](docs/CSV_FORMATS.md) for required columns and currency rules. Values in money columns are rupees with up to two decimals; a source header ending in `_paise` is interpreted as integer paise. ISO 8601 timestamps are normalized to UTC. There is a 5 MB / 10,000 row limit per upload. Import history records source hashes, counts and validation errors.

Current live checks identify same-time inventory disagreements, damaged-return stock questions, excessive refunds, fulfillment failures, payout differences, missing bank imports, unmatched bank credits and conflicting source identifiers. Findings retain imported row IDs and a classification: confirmed inconsistency, suspicious discrepancy, or explained timing difference. The Money Trail reconciles capture, fees, current and prior-period refunds, settlement and bank credit in integer paise.

An investigator can read records only within its authenticated workspace. With `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`, it offers bounded read-only tool calls to a compatible chat-completion model and validates cited source IDs. Without working credentials it stores a visibly labeled deterministic fallback. Model output never changes authoritative inventory or finance records. Scenario Lab freezes observed source versions at a chosen ingestion timestamp, validates references, and runs three deterministic branches with bounded estimates for demand, restock, inspection and cost. It stores branch events for replay, supports event-level differences and seed-based sensitivity, records a preferred plan for human review, and can verify a sandbox replay against the saved snapshot. The historical backtest evaluates only observed order-volume projection where at least three prior ingestion windows and a later observed window exist. Forecast revenue is explicitly hypothetical; no provider operation is performed.

## Synthetic Demo Mode

Visit `/demo` or follow the link on the sign-in screen. **Launch Demo** creates a separate Aaranya Commerce run with 40 SKUs, initial orders, suppliers, matched payouts and costs. Inject a damaged return, settlement false alarm, supplier delay or missing payout. Compare three isolated branches, sweep manual-review delay, approve a sandbox action, export a printable receipt, inspect evidence and traces, then replay the ledger. **Guided tour** runs this path from a fresh deterministic seed. Synthetic records never appear in Live Operations.

The demo event store currently uses SQLite and its own append-only event ledger. Pure reducers enforce stock, refund and settlement rules; branches remap causal IDs and leave their parent unchanged. Read-only WebSocket catch-up uses event sequence numbers.

## Tests

```bash
python -m pytest apps/api/tests -q
pnpm --dir apps/web build
pnpm --dir apps/web exec playwright test
python scripts/benchmark.py --seed 2026 --output reports/benchmark.json
POSTGRES_TEST_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DISPOSABLE_DB python scripts/benchmark_live_detection.py
```

The standard API suite uses SQLite for fast contract checks. With `POSTGRES_TEST_URL` set to a disposable PostgreSQL database, the runtime tests also run the real Alembic migration, append-only trigger, privacy-redaction exception, unique constraint and concurrent webhook checks. These passed locally against PostgreSQL 14 in WSL. The Playwright showcase uses the real API and PostgreSQL for the live CSV-to-sandbox journey, while the separate demo uses its SQLite ledger. CI provisions PostgreSQL 16 for both API and browser jobs; remote CI execution has not yet been observed here. No development-store, Razorpay test-account or model credentials were available locally. The [synthetic benchmark](reports/benchmark.json) covers 20 seeded runs across four injected incident types. A separate [labeled live-detector fixture report](reports/live_detection_fixtures.json) covers seven positive and nine negative CSV workspaces on PostgreSQL. Neither report claims AI uplift or merchant-population accuracy.

Desktop and mobile browser captures are in [docs/screenshots](docs/screenshots). The showcase test regenerates them from completed real-browser interactions; the V2 incident-to-decision test also captures the printable receipt.

## Architecture

Live records use SQLAlchemy models and Alembic migrations for PostgreSQL. Tables include users, workspaces, memberships, import batches, source projections, append-only observations, provider connections, webhook receipts, incidents, investigations, snapshots, scenario results and audit records. PostgreSQL rejects ordinary observation updates and deletes through a trigger; Shopify privacy redaction uses a transaction-scoped delete exception. Passwords use Argon2 through `pwdlib`; signed sessions use PyJWT in an HttpOnly cookie. Authenticated write requests enforce an allowed Origin. Live queries include membership and workspace checks. Incident acknowledgement and review create audit records; review does not execute source-system changes. The Record History screen distinguishes imported/provider observations, audit actions and hypothetical scenario replay. Observed history starts at the first ingestion; it cannot infer earlier business events.

The existing demo SQLite ledger remains separate; old synthetic runs are intentionally not copied into live workspaces. An opt-in reproducible fixture is available with `python scripts/seed_demo_300.py --seed 2026`; it creates a paused, 300-order synthetic run in the configured demo SQLite database. No real refunds, payouts, bank transfers or stock changes are performed.

## Current limitations

- **External integrations are not yet verified with live accounts.** Shopify and Razorpay adapter contracts, signed Shopify and Razorpay webhooks, and model-response validation were tested locally. No development-store installation, Razorpay test-account sync or webhook delivery, or real model inference was run because credentials were unavailable. Model accuracy, latency, and cost therefore remain unmeasured.
- **This is not a production deployment.** PostgreSQL 14 and the real API passed local tests, including a Chromium browser journey, but a managed database, remote CI run, public hosting, failover, and live provider connectivity have not been verified. Public operation still needs managed secrets, HTTPS and secure cookies, controlled registration, distributed rate limits, database network restrictions, background job limits, retention, and monitoring.
- **Evaluation samples remain curated.** Detection precision and recall were measured on 16 hand-labeled CSV workspaces (seven positive and nine negative); replay and recovery checks used 20 seeded runs across four synthetic incident types. These results do not establish accuracy across merchants or real provider data. See the [validation report](docs/VALIDATION_AND_LIMITATIONS.md) for counts and methods.
- **Source history and sync coverage still have boundaries.** Observed history starts at first ingestion. Shopify nested GraphQL reads now paginate, but standard `read_orders` limits older history unless the app is approved and reauthorized for `read_all_orders`; a top-level or nested connection over 10,000 records fails explicitly. Compose schedules read-only Razorpay polling, dense historical reads are partitioned, and a signed webhook receiver is available. Merchant-side webhook registration and real provider retention/completeness remain unverified.
- **The two experiences differ.** Both compare three branches, but the synthetic demo uses an event ledger and delayed manual-review policy, while authenticated Live Operations uses imported observations and an inventory-policy simulator. The demo has a Settings screen; Live Operations has no dedicated workspace-settings page. Every recovery action and provider-facing outcome is sandbox-only; OpsVerse does not make real financial or inventory writes.

## Future scope

The following work is proposed and **not completed** in this release:

1. Validate Shopify installation, GraphQL pagination, and webhook delivery with a development store; validate Razorpay payment, refund, and settlement reads with a test merchant; measure real model findings, latency, and cost with a configured endpoint.
2. Prepare a public deployment with managed PostgreSQL, secret storage, HTTPS, access controls, distributed rate limiting, background job concurrency limits, retention, monitoring, and failover tests. Run the configured CI pipeline remotely and add deployment checks.
3. Add dedicated workspace settings to Live Operations and expand the recovery-policy engine beyond its current inventory and delayed-review models. Keep simulated actions isolated from merchant systems.
4. Expand labeled evaluation across more merchants, incident types, negative controls, and financial edge cases before making broader detection or AI-performance claims.

See [architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md), [product requirements](docs/PRD.md), and [gap analysis](docs/LIVE_OPERATIONS_GAP_ANALYSIS.md). The initial simulator took general inspiration from the [event-sourced WMS prototype](https://github.com/sri-pranav-r/event-sourced-wms) and [financial reconciliation agent](https://github.com/Manu6259/financial-reconciliation-agent); no code or branding was copied.
