# Validation and remaining limits (2026-10-17)

## What was exercised

| Surface | Environment | Evidence |
| --- | --- | --- |
| Live schema | Empty PostgreSQL 14 database in local WSL | All Alembic revisions upgraded to head. |
| Observation ledger | PostgreSQL 14 | UPDATE and DELETE rejected by the append-only trigger; duplicate idempotency key rejected; signed Shopify privacy redaction removed the scoped source records. |
| Webhook concurrency | PostgreSQL 14 | Twelve signed requests across six threads produced one delivery row and one nonduplicate acknowledgement. |
| Workspace boundary | PostgreSQL 14 | Sixteen concurrent own/cross-workspace dashboard reads returned the expected 200/404 pattern. Eight additional read routes returned 404 across the boundary, and both investigator read tools rejected or omitted another workspace's source record. |
| Live commerce flow | Chromium, real FastAPI server, PostgreSQL 14 | Account creation, six CSV batches, imported projections, source-backed inventory incident, deterministic investigator, snapshot, three branches, plan review and sandbox verification. The Playwright showcase test records desktop and mobile screenshots. |
| Synthetic demo | Chromium, real FastAPI server, isolated SQLite ledger | Launch, incident injection, branch comparison, sandbox action, replay and guided tour are exercised by browser tests. |
| Model integration | Contract tests with simulated model HTTP responses | Multi-step allowed tool calls, source/figure validation, malicious output fallback, payload redaction and model URL checks. |
| Labeled live-detector fixtures | PostgreSQL 14, 16 distinct CSV workspaces, regenerated after V2 | Seven positive and nine negative controls across inventory, fulfillment, returns, refunds and payouts; fixture-level precision 7/7, recall 7/7, cause-kind plus source-evidence match 7/7, and exact money status/amount checks 5/5. See the [report](../reports/live_detection_fixtures.json). |

The V2 full API run passed **46/46 tests** with PostgreSQL enabled, including Shopify pagination, Razorpay sync, scheduler, signed webhook checks, evidence graphs, bounded scenarios, backtesting and receipts. The complete Chromium browser suite passed **9/9** against PostgreSQL for authenticated live journeys and the isolated SQLite demo; a focused demo screenshot audit passed **1/1** after a replay-label polish. The final web type check and production build passed. The browser audit saved desktop and mobile screenshots, plus V2 incident and receipt captures in [the screenshot index](screenshots/README.md).

The PostgreSQL browser run uses synthetic *input records in real CSV format* and the real application stack. It does not use mocked frontend responses. CI has a PostgreSQL 16 service, but a remote CI run has not yet been observed in this workspace.

The regenerated reproducible demo benchmark uses 20 seeded runs across four injected positive incident types (five seeds each). It measured 20/20 successful sandbox action verifications, 20/20 persisted event replays consistent after a second SQLite connection read, and 20/20 comparisons with three distinct branch runs and unchanged parents. Fifteen incidents resolve; the missing-payout disputes remain open by design. Repeated seeds probe deterministic stability and are not independent merchant labels. The live browser fixture reconciles one payment path exactly: ₹1,000 capture − ₹23 fees and tax = ₹977 settlement = ₹977 bank credit.

## Provider verification matrix

| Integration | Verified locally | Requires external account to verify |
| --- | --- | --- |
| Shopify | OAuth/HMAC and GraphQL adapter contract tests, nested cursor pagination, signed webhook processing against PostgreSQL, privacy redaction | Real app installation, live Admin GraphQL pagination, approved older-order scope and webhooks from a development store. |
| Razorpay | Read-only adapter contract tests, dense-window partitioning, reconciliation backlog, due-sync worker, signed and deduplicated webhook fixture, integer-paise import and reconciliation through CSV | Test merchant authorization, live payments/refund/settlement reads and actual webhook delivery; run and monitor the Compose worker or an external scheduler. |
| Compatible model endpoint | Tool orchestration and validation with simulated HTTP responses | Successful inference, latency and cost against configured endpoint credentials. |

## Product and deployment limits

- Live observed history begins at the first ingestion. It cannot reconstruct earlier changes.
- Shopify's default order scope has a limited historical window. Approved `read_all_orders` can be requested with `SHOPIFY_REQUEST_ALL_ORDERS=true` and store reauthorization. Top-level and nested GraphQL connections now paginate and fail explicitly over 10,000 records. Shopify webhook subscriptions must be configured in Shopify.
- Razorpay sync can be run manually, by the Compose `sync` worker, or by an external scheduler invoking `scripts/sync_due_providers.py`. A signed webhook receiver is implemented, but the merchant must configure the public URL and matching secret in Razorpay. Reads use an overlapping polling window. Dense historical reads split by time, and reconciliation days continue across runs. The Compose worker and webhook delivery have not been exercised with Docker or a Razorpay account here; live retention, provider limits, and completeness remain unverified.
- The public synthetic demo now saves three branches: no intervention, immediate typed recovery and delayed manual review. A one-seed delay sweep measures modeled recovery time. The authenticated live Decision Lab compares three bounded inventory policies and can repeat a selected assumption across multiple seeds. Neither mode writes to external providers.
- The historical lab evaluates newly observed order-volume projection only when ingestion windows are sufficient. It does not validate counterfactual policy effects, inventory ending balances, returns, settlement timing or recovery time against merchant outcomes. A fresh imported CSV cannot supply an earlier historical cutoff, even when its business timestamps are old.
- Reconciliation codes identify only supported evidence. `CROSS_PERIOD_REFUND`, `MISSING_BANK_CREDIT`, `MISSING_SOURCE_DATA`, and `UNEXPLAINED_VARIANCE` are emitted where justified. Fee variance, duplicate transaction, FX difference and settlement delay remain unclassified unless a source-backed rule is available; the system does not invent those causes.
- Printable Decision Receipts use browser print for PDF. They include saved run IDs, source observation IDs, review and sandbox verification; no server-side PDF generator is present.
- Live Operations has workspace selection and account sign-out, but no dedicated workspace-settings page. Demo Mode has a simulation Settings page.
- When no model endpoint is configured, live investigation uses a clearly labeled deterministic fallback. The public synthetic demo investigator is deterministic.
- The 16 labeled CSV workspaces report fixture-level precision, recall, cause-kind/evidence matching and five money checks. They remain curated and cannot estimate merchant-population accuracy. Actual model root-cause accuracy, latency and cost remain unmeasured because no model endpoint was configured. The separate synthetic benchmark contains 20 seeded runs across four injected positive incident types.
- Public deployment still needs operator-managed secrets, HTTPS and secure cookies, registration controls, rate limiting, database network restrictions, background job concurrency limits, retention/cleanup policy and monitoring. No production deployment or failover test has been performed.
- Expensive live analysis routes now have a local per-process minute budget. This does not coordinate multiple API workers or replace edge/distributed rate limiting for public deployment.
- The Money Trail endpoint now bulk-loads linked source records instead of querying once per payment. No representative large-merchant load test was run, so production latency and throughput remain unmeasured.
- PostgreSQL validation used a disposable WSL database. A managed PostgreSQL service and external providers remain unverified.
