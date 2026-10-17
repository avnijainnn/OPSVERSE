# Live Operations gap analysis

Reviewed 2026-10-15 against the product correction. The existing implementation is a synthetic, single-business simulator. Its data must never be represented as an imported or connected business.

| Area | Current state | Classification | Required change |
| --- | --- | --- | --- |
| Deterministic event reducers, replay, branching, four incident demos | Working and covered by API/browser tests | Functional demo | Preserve behind an explicit Demo Mode boundary. |
| Demo investigation and approval | Typed reads, evidence IDs, deterministic checks, optional model wording | Partial | Keep for demo; build workspace-scoped investigation over imported source records. |
| Live orders, products, inventory, returns, finance | No ingestion or source provenance | Missing | Add transactional CSV imports, record validation, history and derived dashboards. |
| Shopify and Razorpay | No credentials, OAuth, synchronization or webhooks | Missing | Add official read-only adapters after the import path; verify with test accounts before calling them live. |
| Persistence | SQLite bootstrap for demo events and traces | Requires migration | Use PostgreSQL through SQLAlchemy and Alembic for live records, users, workspaces, imports, incidents and snapshots; preserve legacy demo data during transition. |
| Authentication and tenancy | No accounts or authorization; demo endpoints are public | Missing | Add supported password hashing, signed sessions, workspace ownership and protected live APIs. |
| Real incident detection | Only injected synthetic incidents | Simulated substitute | Add rules over imports, persist classifications, source references and investigation history. |
| Real-data scenario lab | Branches only synthetic runs | Missing | Snapshot a workspace's imported state and run isolated deterministic scenarios without writing source records. |
| Live UI | Every screen currently reads the synthetic `/runs` API | Simulated substitute | Give Live Operations a separate route, account state, import workflow, dashboard, incidents, Money Trail, scenarios and integration status. |
| Benchmarks | Four injected positive demo cases; precision and recall unavailable | Partial | Add labeled negative import cases and real-data evaluation before quoting detection accuracy. |

## Implementation order

1. Establish the PostgreSQL schema and migration path, accounts, sessions, workspaces and authorization tests.
2. Build CSV imports, validation, column mapping, duplicate detection, import history, finance reconciliation and real-data detection.
3. Add workspace-scoped investigation, source-linked findings and isolated snapshots for Scenario Lab.
4. Connect Live Operations UI with clear disconnected, loading and error states; move the synthetic experience to a labeled Demo Mode.
5. Add Shopify and Razorpay read-only adapters, webhook verification, contract tests and external-account verification when credentials are available.
6. Expand repeatable tests and benchmarks, migrate remaining demo persistence, and review production deployment controls.

Completion claims must follow passing tests. Credentials and a live provider test store are not available in this repository, so external sync cannot be described as verified until exercised against one.
