"""Run once from cron or a hosted scheduler; no merchant write APIs are called."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
from app.provider_schedule import sync_due  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["shopify", "razorpay"])
    parser.add_argument("--interval-minutes", type=int, default=15)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(sync_due(interval_minutes=args.interval_minutes,
                              limit=args.limit, provider=args.provider), indent=2))
