# OpsVerse cofounder walkthrough

Open `/demo`. This isolated synthetic environment needs no provider credentials. **Guided tour** uses the same application actions as the manual path.

| Step | Action | What to inspect |
| --- | --- | --- |
| 1 | **Launch Demo**. Watch the business network and open an event. | Every movement comes from the saved SQLite event ledger. |
| 2 | Inject a **Damaged return** and open the incident. | The causal graph links the incident to exact synthetic source events and a deterministic investigator trace. |
| 3 | **Compare futures**. | Three isolated runs start at one fork: no intervention, immediate typed recovery and delayed manual review. |
| 4 | **Run delay sweep** for 0, 24 and 48 hours. | New branch ledgers produce measured recovery times. This is one deterministic seed, not a confidence interval. |
| 5 | **Approve in sandbox** and open the **Decision Receipt**. | The receipt lists evidence, run IDs, decision status, verification and a reproducibility manifest. Nothing writes to a merchant. |
| 6 | Open **Replay**, step through recorded events, then use **Launch Demo** to reset. | The same seed rebuilds the same business state. |

For the authenticated path, open `/live`, create a workspace and import the CSV examples in [CSV formats](CSV_FORMATS.md). The Command Center shows observed records. The Incident Microscope links source observation versions. The Decision Lab freezes an observed snapshot, compares three policies, runs seed sensitivity and event difference queries, reports insufficient historical coverage when appropriate, then records a preferred plan and sandbox verification before printing a receipt. The detailed steps are in [V2 reproducibility](V2_REPRODUCIBILITY.md).

## Current limits

- Shopify, Razorpay and actual model inference were not tested against external credentials. The investigator uses a labeled deterministic fallback when no model is configured.
- The synthetic demo and authenticated live workspaces are separate data products. The demo uses SQLite, while the live validation path uses PostgreSQL.
- Detection evaluation uses 16 curated CSV workspaces; replay and recovery evaluation uses 20 seeded synthetic runs. These samples do not estimate accuracy across merchants. See [validation and limits](VALIDATION_AND_LIMITATIONS.md).
- The historical lab currently evaluates order-volume projection only when enough observations were ingested before each cutoff. It does not establish causal policy success.
- Production deployment still needs managed secrets, HTTPS, registration controls, rate limits, background-worker limits, monitoring and failover verification.
