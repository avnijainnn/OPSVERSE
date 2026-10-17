"""Leakage-safe evaluation of observed order-arrival demand projections.

This evaluates one narrow simulator input (order volume), not the causal success of
counterfactual recovery policies. Both business time and ingestion time must qualify.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from .live_twin import utc


def _count_orders(observations: list, start: datetime, end: datetime,
                  known_by: datetime) -> int:
    identities = set()
    for item in observations:
        if item.object_kind != "order" or not item.external_at:
            continue
        if start <= utc(item.external_at) < end and utc(item.ingested_at) <= known_by:
            identities.add((item.source_system, item.provider_object_id))
    return len(identities)


def error_summary(pairs: list[tuple[float, float]]) -> dict:
    if not pairs:
        return {"samples": 0, "mae": None, "rmse": None, "bias": None, "normalized_mae": None}
    errors = [forecast - actual for forecast, actual in pairs]
    scale = sum(abs(actual) for _, actual in pairs) / len(pairs)
    mae = sum(abs(value) for value in errors) / len(errors)
    return {"samples": len(pairs), "mae": mae,
            "rmse": math.sqrt(sum(value * value for value in errors) / len(errors)),
            "bias": sum(errors) / len(errors),
            "normalized_mae": mae / scale if scale else None}


def evaluate_order_backtest(observations: list, cutoff: datetime, horizon_hours: int,
                            max_origins: int = 12) -> dict:
    if cutoff.tzinfo is None or not 1 <= horizon_hours <= 720:
        raise ValueError("Use a timezone-aware cutoff and a horizon of 1–720 hours")
    cutoff = utc(cutoff)
    horizon = timedelta(hours=horizon_hours)
    ordered = sorted(observations, key=lambda item: (utc(item.ingested_at), item.id))
    order_history = [item for item in ordered if item.object_kind == "order" and item.external_at]
    first = utc(order_history[0].ingested_at) if order_history else None
    latest = utc(order_history[-1].ingested_at) if order_history else None
    origins = []
    cursor = cutoff
    for _ in range(max_origins):
        if first is None or latest is None or cursor - 3 * horizon < first or cursor + horizon > latest:
            break
        history = [_count_orders(ordered, cursor - (index + 1) * horizon,
                                 cursor - index * horizon, cursor) for index in range(3)]
        actual = _count_orders(ordered, cursor, cursor + horizon, latest)
        weighted = (5 * history[0] + 3 * history[1] + 2 * history[2]) / 10
        origins.append({"cutoff": cursor.isoformat(), "horizon_end": (cursor + horizon).isoformat(),
                        "known_history": history, "prediction": weighted, "actual": actual,
                        "baselines": {"last_observed": history[0],
                                      "three_window_average": sum(history) / 3},
                        "known_observation_count": sum(utc(item.ingested_at) <= cursor for item in ordered),
                        "future_observations_excluded_from_prediction": sum(utc(item.ingested_at) > cursor for item in ordered)})
        cursor += horizon
    if not origins:
        return {"status": "insufficient_history", "message": "Insufficient observed history for a valid backtest.",
                "first_ingested_at": first.isoformat() if first else None,
                "last_ingested_at": latest.isoformat() if latest else None,
                "required_prior_windows": 3, "horizon_hours": horizon_hours,
                "evaluated_samples": 0}
    projected = [(row["prediction"], row["actual"]) for row in origins]
    return {"status": "evaluated", "metric": "newly observed order volume",
            "model_version": "weighted-demand-v1", "horizon_hours": horizon_hours,
            "cutoff": cutoff.isoformat(), "evaluated_samples": len(origins),
            "source_coverage": {"first_ingested_at": first.isoformat(),
                                "last_ingested_at": latest.isoformat(),
                                "order_observations": sum(item.object_kind == "order" for item in ordered),
                                "basis": "Business event time and ingestion time; unobserved provider events are unknown"},
            "errors": error_summary(projected),
            "baselines": {name: error_summary([(row["baselines"][name], row["actual"]) for row in origins])
                          for name in ("last_observed", "three_window_average")},
            "origins": origins,
            "limitations": ["Order volume is one simulator input, not a validated counterfactual outcome",
                            "Only records ingested by each cutoff enter predictions",
                            "Unknown source outages or late imports can make observed actuals incomplete",
                            "Inventory, settlement timing, returns, and recovery time are not evaluated by this model"]}
