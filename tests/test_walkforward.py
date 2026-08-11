from __future__ import annotations

import numpy as np
import pandas as pd

from calibration.walkforward import (
    attach_forward_labels,
    build_score_history,
    evaluate_score_walk_forward,
    expanding_walk_forward,
    score_bucket_summary,
)
from providers.demo import DemoProvider


def test_walk_forward_never_trains_on_test_or_gap():
    folds = expanding_walk_forward(120, min_train_size=60, test_size=20, gap=5)
    assert len(folds) == 3
    for fold in folds:
        assert fold.train_end < fold.test_start
        assert fold.test_start - fold.train_end - 1 >= 5


def test_forward_labels_use_strictly_future_dates():
    scores = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]), "score": [50, 60, 55, 58]})
    opp = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]), "opportunity_net_return_pct": [100.0, 1.0, 2.0, 3.0]})
    labeled = attach_forward_labels(scores, opp, horizon=2)
    assert labeled.iloc[0]["forward_opportunity_pct"] == 1.5
    assert labeled.iloc[1]["forward_opportunity_pct"] == 2.5


def test_forward_labels_do_not_jump_across_missing_minute_dates():
    scores = pd.DataFrame({
        "date": pd.bdate_range("2026-01-01", periods=8),
        "score": range(8),
    })
    opp = pd.DataFrame({
        "date": [scores.iloc[1]["date"], scores.iloc[3]["date"], scores.iloc[4]["date"]],
        "opportunity_net_return_pct": [1.0, 99.0, 2.0],
    })
    labeled = attach_forward_labels(scores, opp, horizon=2)
    assert pd.isna(labeled.iloc[0]["forward_opportunity_pct"])
    assert labeled.iloc[0]["forward_days"] == 1


def test_forward_labels_can_use_market_calendar_to_expose_stock_date_gap():
    market_dates = pd.bdate_range("2026-01-01", periods=5)
    scores = pd.DataFrame({"date": [market_dates[0], market_dates[2], market_dates[3]], "score": [50, 60, 70]})
    opp = pd.DataFrame({
        "date": [market_dates[2], market_dates[3]],
        "opportunity_net_return_pct": [1.0, 2.0],
    })
    labeled = attach_forward_labels(scores, opp, horizon=2, trading_calendar=market_dates)
    assert pd.isna(labeled.iloc[0]["forward_opportunity_pct"])
    assert labeled.iloc[0]["forward_days"] == 1


def test_score_history_is_unchanged_when_only_future_bars_change():
    p = DemoProvider()
    daily = p.history("300059", "2025-01-01", "2026-08-07", interval="1d")
    first = build_score_history("300059", daily, lookback=60)
    cutoff = pd.Timestamp(first.iloc[20]["date"])
    altered = daily.copy()
    mask = pd.to_datetime(altered["datetime"]) > cutoff
    altered.loc[mask, ["open", "high", "low", "close"]] *= 5.0
    second = build_score_history("300059", altered, lookback=60)
    left = first[first["date"] <= cutoff][["date", "score"]].reset_index(drop=True)
    right = second[second["date"] <= cutoff][["date", "score"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)


def test_bucket_and_oos_evaluation_shapes():
    n = 140
    frame = pd.DataFrame({"date": pd.bdate_range("2025-01-01", periods=n), "score": np.linspace(40, 90, n), "forward_opportunity_pct": np.linspace(0.2, 2.0, n)})
    buckets = score_bucket_summary(frame, buckets=5)
    assert len(buckets) == 5
    assert buckets.iloc[-1]["avg_forward_opportunity_pct"] > buckets.iloc[0]["avg_forward_opportunity_pct"]
    metrics, oos = evaluate_score_walk_forward(frame, min_train_size=60, test_size=20, gap=5)
    assert not metrics.empty
    assert not oos.empty
    assert (metrics["test_rows"] > 0).all()
