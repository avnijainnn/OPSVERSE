# Repair and showcase audit

Baseline: `main` at `c8e4671`; working tree initially clean. This checklist records the behavior found in the code before repair. Status is updated as work is verified.

| Issue | Existing behavior | Expected behavior | Affected code | Required change | Regression test | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Zero inspection delay | A truthiness check skips release forever | Zero starts an eligible inspection at simulated time zero; only a valid result can release stock | `live_twin.py` | Explicit inspection state machine and result | Zero delay, failed and hold results | Open |
| Recovery time | First fulfilled demand index is named recovery | Recovery completes only after policy actions and inventory conflicts resolve, or remains unresolved | `live_twin.py` | Clock based recovery fields | Completion and unresolved horizon | Open |
| Lead time | Demand ordinal is compared with supplier lead time | Delivery occurs after elapsed hours from a modeled purchase order | `live_twin.py` | Timestamped event schedule | Demand density independence, delivery beyond horizon | Open |
| Stock accounting | Inspection branch adds low stock plus replenishment, potentially duplicating stock | Stock moves between conserved buckets; supplier adds only purchased quantity once | `live_twin.py` | Bucket ledger and assertions | Conservation, duplicate arrival | Open |
| Metric semantics | Rejected attempts are called delayed; risk is called refund exposure | Distinct fulfilled, partial, lost, risk and cost figures with units | `live_twin.py`, UI, receipt | Event contributions and renamed labels | Metric reconciliation | Open |
| Difference attribution | Only fulfillment differences are shown, including for cost and risk | Saved event contributions exactly reconcile each difference | `live_twin.py`, `live_api.py` | Event-level attribution | Every supported metric | Open |
| Replay | Live scenario view is static cards | Synchronized three-branch ledger playback, divergence and event inspection | `live/page.tsx`, components | Shared playback UI backed by saved events | Playwright replay flow | Open |
| Live network | Live view is mainly record cards; synthetic demo has its own network | Observed source-aware map with honest missing coverage | `live/page.tsx`, `demo/page.tsx` | Reusable network primitives | Browser source inspection | Open |
| AI verification | Tool loop is tested with stubs; real inference has not been demonstrated | Actual call if configured; otherwise explicit fallback and setup | `live_ai.py` | Inspect endpoint, validation, traces | Contrasting financial cases | Open |
| Generated artifacts | TypeScript build info and egg-info are tracked | Build outputs ignored and untracked | `.gitignore`, index | Remove from index | `git ls-files` | Open |

Baseline test and browser results are recorded in the final validation section after execution. Existing Playwright suites cover portions of Live and Demo, but do not exercise synchronized three-futures playback or exact event-to-metric reconciliation.
