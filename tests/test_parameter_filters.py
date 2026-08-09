from __future__ import annotations

import pandas as pd

from scanner.parameter_filters import SpotFilterConfig, ScoreFilterConfig, apply_score_filters, apply_spot_filters


def test_spot_filters_apply_all_enabled_metrics():
    universe = pd.DataFrame([
        {"code": "600001", "name": "A", "market": "SH"},
        {"code": "000001", "name": "B", "market": "SZ"},
        {"code": "920001", "name": "C", "market": "BJ"},
    ])
    quotes = pd.DataFrame([
        {"code": "600001", "market": "SH", "price": 10, "pct_change": 2, "amplitude": 4, "amount": 5e8, "turnover": 2},
        {"code": "000001", "market": "SZ", "price": 10, "pct_change": 9, "amplitude": 4, "amount": 5e8, "turnover": 2},
        {"code": "920001", "market": "BJ", "price": 10, "pct_change": 2, "amplitude": 4, "amount": 1e8, "turnover": 2},
    ])
    cfg = SpotFilterConfig(
        markets=("SH", "SZ", "BJ"),
        min_amount_yi=2,
        min_turnover=1,
        min_amplitude=2,
        max_amplitude=8,
        min_pct_change=-5,
        max_pct_change=5,
        min_price=2,
        max_price=100,
    )
    out = apply_spot_filters(universe, quotes, cfg)
    assert out["code"].tolist() == ["600001"]


def test_disabled_spot_metrics_do_not_drop_nan_rows():
    universe = pd.DataFrame([{"code": "600001", "name": "A", "market": "SH"}])
    quotes = pd.DataFrame([
        {"code": "600001", "market": "SH", "price": float("nan"), "pct_change": float("nan"), "amplitude": float("nan"), "amount": float("nan"), "turnover": float("nan")}
    ])
    out = apply_spot_filters(universe, quotes, SpotFilterConfig(markets=("SH",)))
    assert out["code"].tolist() == ["600001"]


def test_score_filters_apply_history_thresholds():
    ranking = pd.DataFrame([
        {"code": "600001", "score": 75, "avg_amplitude": 4, "avg_intraday_space": 2.5, "median_amount": 8e8, "max_drawdown": -20, "risk_label": "中等"},
        {"code": "000001", "score": 55, "avg_amplitude": 4, "avg_intraday_space": 2.5, "median_amount": 8e8, "max_drawdown": -20, "risk_label": "中等"},
    ])
    cfg = ScoreFilterConfig(min_score=60, min_avg_amplitude=2, max_avg_amplitude=8, min_intraday_space=1.5, min_median_amount_yi=5, max_drawdown_abs=35)
    out = apply_score_filters(ranking, cfg)
    assert out["code"].tolist() == ["600001"]
