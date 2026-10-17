"""Create an opt-in, reproducible 300-order synthetic Demo Mode run."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain import project  # noqa: E402
from app.engine import seed_world  # noqa: E402
from app.store import connection, events, init  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    init()
    with connection() as db:
        run_id = seed_world(db, args.seed, order_count=300)
        state = project(events(db, run_id))
        assert len(state["orders"]) == 300
        db.execute("UPDATE simulation_runs SET status='paused' WHERE run_id=?", (run_id,))
        print(f"Demo run {run_id}: {len(state['orders'])} synthetic orders, seed {args.seed}")


if __name__ == "__main__":
    main()
