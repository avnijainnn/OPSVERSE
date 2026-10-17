from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.live_backtest import error_summary, evaluate_order_backtest


def observation(number, event_time, ingested_at, kind="order"):
    return SimpleNamespace(id=str(number), object_kind=kind, external_at=event_time,
                           ingested_at=ingested_at, source_system="fixture",
                           provider_object_id=f"order-{number}")


def test_backtest_uses_only_information_ingested_by_each_cutoff():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    history = [observation(0, start, start)]
    history += [observation(n, start + timedelta(hours=n) + timedelta(minutes=5),
                            start + timedelta(hours=n) + timedelta(minutes=6)) for n in range(1, 10)]
    # This old business event was imported after the requested cutoff; it cannot enter prediction.
    history.append(observation(99, start + timedelta(hours=2, minutes=20),
                               start + timedelta(hours=8, minutes=30)))
    cutoff = start + timedelta(hours=4)
    result = evaluate_order_backtest(history, cutoff, 1)
    assert result["status"] == "evaluated"
    assert result["origins"][0]["known_history"] == [1, 1, 1]
    assert result["origins"][0]["prediction"] == 1
    assert result["origins"][0]["future_observations_excluded_from_prediction"] > 0
    assert result == evaluate_order_backtest(list(reversed(history)), cutoff, 1)


def test_backtest_insufficient_and_zero_scale():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert evaluate_order_backtest([observation(1, start, start)], start, 24)["status"] == "insufficient_history"
    summary = error_summary([(0, 0), (2, 0)])
    assert summary == {"samples": 2, "mae": 1, "rmse": 2 ** .5,
                       "bias": 1, "normalized_mae": None}
