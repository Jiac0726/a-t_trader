from __future__ import annotations

import pandas as pd

from backtest.t_engine import CostModel, best_single_t_envelope, mean_reversion_backtest, resolve_t_shares, summarize_trades


def make_intraday(prices: list[float], day: str = "2026-08-07") -> pd.DataFrame:
    idx = pd.date_range(f"{day} 09:30", periods=len(prices), freq="5min")
    return pd.DataFrame(
        {
            "datetime": idx,
            "open": prices,
            "high": [p * 1.001 for p in prices],
            "low": [p * 0.999 for p in prices],
            "close": prices,
            "volume": 100000,
            "amount": [p * 100000 for p in prices],
        }
    )


def test_t1_quantity_is_capped_by_bottom_position():
    assert resolve_t_shares(1000, 0.5) == 500
    assert resolve_t_shares(350, 1.0) == 300
    assert resolve_t_shares(80, 1.0) == 0


def test_positive_and_reverse_hindsight_envelopes_find_ordered_move():
    costs = CostModel(commission_rate=0, min_commission=0, stamp_duty_sell_rate=0, slippage_bps=0)
    positive = best_single_t_envelope(make_intraday([10, 9, 8, 9, 10, 11]), "positive", 1000, 0.5, costs)
    reverse = best_single_t_envelope(make_intraday([10, 11, 12, 11, 10, 9]), "reverse", 1000, 0.5, costs)
    assert positive.iloc[0]["net_pnl"] == 1500
    assert reverse.iloc[0]["net_pnl"] == 1500


def test_costs_reduce_envelope_and_summary_is_consistent():
    bars = make_intraday([10, 9.8, 9.5, 9.7, 10.0, 10.2])
    free = best_single_t_envelope(bars, "positive", 1000, 0.5, CostModel(0, 0, 0, 0, 0))
    paid = best_single_t_envelope(bars, "positive", 1000, 0.5, CostModel())
    assert paid.iloc[0]["net_pnl"] < free.iloc[0]["net_pnl"]
    summary = summarize_trades(paid)
    assert summary.trades == 1


def test_causal_mean_reversion_executes_after_signal():
    prices = [10, 10.1, 10.0, 10.05, 10.0, 9.5, 9.2, 9.4, 9.7, 10.0, 10.1, 10.0]
    costs = CostModel(commission_rate=0, min_commission=0, stamp_duty_sell_rate=0, slippage_bps=0)
    trades = mean_reversion_backtest(
        make_intraday(prices),
        "positive",
        bottom_shares=1000,
        t_ratio=0.5,
        costs=costs,
        window=4,
        entry_z=1.0,
    )
    assert len(trades) == 1
    assert trades.iloc[0]["strategy"] == "rolling_z_mean_reversion"
    assert trades.iloc[0]["shares"] == 500
