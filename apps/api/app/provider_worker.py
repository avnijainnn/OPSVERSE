"""Periodic read-only provider polling for the Compose deployment."""
from __future__ import annotations

import logging
import os
import time

from .provider_schedule import sync_due


def positive_int(name: str, default: int, upper: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= value <= upper:
        raise ValueError(f"{name} must be between 1 and {upper}")
    return value


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    interval = positive_int("PROVIDER_SYNC_INTERVAL_MINUTES", 15, 1440)
    poll_seconds = positive_int("PROVIDER_WORKER_POLL_SECONDS", 60, 3600)
    while True:
        try:
            for result in sync_due(interval_minutes=interval, limit=100):
                logging.info("provider sync: %s", result)
        except Exception:
            logging.exception("provider sync sweep failed; retrying after poll interval")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
