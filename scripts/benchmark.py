"""Run reproducible, measured synthetic evaluations.

From repository root: python scripts/benchmark.py --seed 2026 --output report.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from app import store  # noqa: E402
from app.domain import project  # noqa: E402
from app.engine import compare, decide, inject, seed_world  # noqa: E402


def evaluate(seed: int, seeds_per_kind: int = 5) -> dict:
    if not 1 <= seeds_per_kind <= 100:
        raise ValueError("Choose 1-100 seeds per incident type")
    cases = []
    with tempfile.TemporaryDirectory() as folder:
        store.DB_PATH = Path(folder) / "benchmark.db"
        store.init()
        with store.connection() as db:
            for offset in range(seeds_per_kind):
                case_seed = seed + offset
                for kind in ("damaged_return", "settlement_mismatch", "supplier_delay", "missing_payout"):
                    start = time.perf_counter()
                    run_id = seed_world(db, case_seed)
                    incident = inject(db, run_id, kind)
                    before = store.events(db, run_id)
                    branches = compare(db, incident["incident_id"])
                    parent_unchanged = store.events(db, run_id) == before
                    result = decide(db, incident["incident_id"], True, len(before))
                    current = store.events(db, run_id)
                    db.commit()
                    with store.connection(write=False) as replay_db:
                        persisted_events = store.events(replay_db, run_id)
                    replay_consistent = project(current) == project(persisted_events)
                    cases.append({"type": kind, "seed": case_seed, "run_id": run_id,
                        "events": len(current), "evidence_count": len(incident["evidence_event_ids"]),
                        "parent_unchanged_during_comparison": parent_unchanged,
                        "baseline": branches["baseline_metrics"], "recovery": branches["recovery_metrics"],
                        "delayed_review": branches["third_metrics"],
                        "three_distinct_branch_runs": len({branches["baseline_run_id"],
                                                           branches["recovery_run_id"],
                                                           branches["third_run_id"]}) == 3,
                        "verification_passed": bool(result["verification"][0]["passed"]),
                        "incident_resolved": result["status"] == "resolved",
                        "replay_consistent": replay_consistent,
                        "duration_ms": round((time.perf_counter() - start)*1000, 1)})
    return {"evaluated_at": datetime.now(timezone.utc).isoformat(), "sample_size": len(cases),
            "configuration": {"seed": seed, "seeds_per_kind": seeds_per_kind,
                              "mode": "deterministic_demo",
                              "replay_check": "re-read committed events through a separate SQLite connection"},
            "action_verification_rate": sum(c["verification_passed"] for c in cases)/len(cases),
            "incident_resolution_rate": sum(c["incident_resolved"] for c in cases)/len(cases),
            "replay_consistency_rate": sum(c["replay_consistent"] for c in cases)/len(cases),
            "three_branch_isolation_rate": sum(c["three_distinct_branch_runs"] and c["parent_unchanged_during_comparison"]
                                               for c in cases)/len(cases),
            "detection_precision": None, "detection_recall": None,
            "limitations": (f"{len(cases)} seeded runs across four injected positive incident types. "
                            "Repeated seeds test replay and verification stability, not independent merchant accuracy. "
                            "Detection precision/recall and AI uplift are not measured by this synthetic suite."),
            "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--seeds-per-kind", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.seed, args.seeds_per_kind)
    output = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(args.output)
        canonical = Path(__file__).resolve().parents[1] / "reports" / "benchmark.json"
        if args.output.resolve() == canonical:
            browser_report = canonical.parents[1] / "apps" / "web" / "public" / "benchmark.json"
            browser_report.write_text(output, encoding="utf-8")
            print(browser_report)
    else:
        print(output)
