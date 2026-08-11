from __future__ import annotations

import numpy as np
import pandas as pd

from features.regime import attach_market_regime, classify_price_regime


def _daily(prices) -> pd.DataFrame:
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"datetime": pd.bdate_range("2025-01-01", periods=len(p)), "close": p})


def test_future_prices_do_not_change_past_regimes():
    prices = 100 * np.exp(np.cumsum(np.linspace(-0.002, 0.003, 220)))
    first = classify_price_regime(_daily(prices), vol_rank_min_periods=20)
    cutoff = first.iloc[150]["date"]
    altered = prices.copy()
    altered[151:] *= np.linspace(1, 4, len(altered) - 151)
    second = classify_price_regime(_daily(altered), vol_rank_min_periods=20)
    left = first[first["date"] <= cutoff].reset_index(drop=True)
    right = second[second["date"] <= cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)


def test_smooth_rising_series_becomes_trend_up():
    out = classify_price_regime(_daily(np.linspace(100, 150, 220)), vol_rank_min_periods=20, high_vol_percentile=0.99)
    assert (out["regime"].dropna().tail(60) == "trend_up").mean() > 0.8


def test_smooth_falling_series_becomes_trend_down():
    out = classify_price_regime(_daily(np.linspace(150, 100, 220)), vol_rank_min_periods=20, high_vol_percentile=0.99)
    assert (out["regime"].dropna().tail(60) == "trend_down").mean() > 0.8


def test_oscillating_low_efficiency_series_is_range():
    t = np.arange(240)
    out = classify_price_regime(_daily(100 + 2 * np.sin(t / 2.5)), vol_rank_min_periods=20, high_vol_percentile=0.99, min_trend_return_pct=1.0)
    assert (out["regime"].dropna().tail(80) == "range").mean() > 0.7


def test_recent_volatility_shock_is_high_vol():
    rng = np.random.default_rng(3)
    prices = 100 * np.exp(np.cumsum(np.r_[rng.normal(0, 0.002, 180), rng.normal(0, 0.04, 50)]))
    out = classify_price_regime(_daily(prices), vol_rank_min_periods=30, high_vol_percentile=0.8)
    assert (out["regime"].dropna().tail(30) == "high_vol").mean() > 0.5


def test_attach_market_regime_drops_unknown_dates_by_default():
    panel = pd.DataFrame({"date": pd.to_datetime(["2026-01-05", "2026-01-06"]), "code": ["600001", "600001"], "score": [1, 2]})
    regimes = pd.DataFrame({"date": pd.to_datetime(["2026-01-05"]), "regime": ["range"]})
    out = attach_market_regime(panel, regimes)
    assert len(out) == 1 and out.iloc[0]["market_regime"] == "range"
