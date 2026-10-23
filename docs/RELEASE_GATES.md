# OpsVerse release gates (production-readiness-phase1)

This branch is **not** proof that OpsVerse is ready for unrestricted public SaaS. It hardens the existing source-backed private pilot without touching the simulated demo's persisted data.

## Implemented in this branch

1. `REGISTRATION_ENABLED=false` on the private-pilot Compose override (inherited from `pilot-hardening`).
2. Workspace write access: owners/analysts can investigate, import, create snapshots, and simulate. Only owners can review a preferred recovery branch or change Shopify/Razorpay provider credentials. Viewers can read authorized workspace records.
3. Login and registration attempt budgets, with optional shared Redis limits. Set `AUTH_REQUIRE_DURABLE_LIMITS=true` when deploying with Redis; failures return 503 rather than allowing unchecked writes. This is a rate-limit backend, not a persistent job queue.
4. Optional `deploy/compose.hardening.yml` Redis service, **in addition to** the existing local and private-live Compose files.
5. Single-host Caddy pilot gate blocking the unauthenticated SQLite demo, and backup/restore smoke-test scripts. Caddy's environment variables must be supplied to the Caddy **service**, not just the API .env file.

## Laptop / developer workflow

Keep your local working-tree changes before switching branches.

```powershell
git fetch origin
git switch production-readiness-phase1
docker compose up -d --build db api sync web
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health
```

Docker local Compose intentionally leaves registration enabled for local use. This is **not a production configuration**.

## Private HTTPS pilot deployment

Run on a properly secured Linux VPS after configuring `.env`, DNS and an HTTPS Caddy service, and **before** adding merchant data:

```bash
docker compose -f docker-compose.yml -f deploy/compose.live.yml -f deploy/compose.hardening.yml config --quiet
docker compose -f docker-compose.yml -f deploy/compose.live.yml -f deploy/compose.hardening.yml up -d --build db redis api sync web
docker compose -f docker-compose.yml -f deploy/compose.live.yml -f deploy/compose.hardening.yml ps
bash deploy/backup-postgres.sh
LATEST=$(ls -t var/backups/*.dump | head -n 1)
bash deploy/restore-test.sh "$LATEST"
```

The backup script produces local archives; operators must encrypt and copy them to independently managed off-host storage and verify recovery objectives. Test on a disposable or expressly authorized database first. Do **not** use `docker compose down -v` on a database holding useful information.

Use `deploy/Caddyfile.pilot.example` rather than public-hosting the development ports. Test that anonymous UI/API paths are denied and that only correctly signed provider webhooks pass the application. Initial account creation requires a deliberately controlled temporary bootstrap when registration is disabled.

## Before inviting unrelated businesses or calling the product public SaaS

**Unfinished and not certified by this branch:**

- Per-visitor session ownership and access controls on **all** synthetic demo routes, including incident IDs, branches and WebSockets; the current synthetic demo is intentionally blocked on the pilot host.
- Secure self-service team invitation/account recovery, verified role changes, dedicated admin interface, customer billing and tenant lifecycle.
- Real Shopify development-store OAuth, scope approval, synchronization, refunds, retries, uninstall/redaction and webhook delivery.
- Real Razorpay test merchant authentication, webhook delivery, payments/refunds and settlement/bank reconciliation.
- Real tool-capable LLM inference and independent evidence-grounding/hallucination tests.
- Per-SKU rolling historical demand validation, larger real workloads, statistically meaningful backtests of recovery outcomes; do not make revenue/savings claims based on hypothetical branches.
- Full cross-domain supplier/returns/payment/fulfillment counterfactual engine from the broader original product vision.
- Off-host backups, restore drills, monitoring/alerts, response/runbook, secret rotation, retention/deletion policies, security review and load testing.
- Distributed concurrency and compute quotas for scenarios, imported data and long-running provider calls; Redis-backed request counters are only one layer.

A passing CI suite verifies the tested code paths, **not** merchant integrations or the hosted environment. Pilot rollout should remain controlled until real operational tests are complete.
