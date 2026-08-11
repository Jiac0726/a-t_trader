from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.t_engine import CostModel, best_single_t_envelope, mean_reversion_backtest, resolve_t_shares, summarize_trades


def make_intraday(prices: list[float], day: str = "2026-08-07") -> pd.DataFrame:
    idx = pd.date_range(f"{day} 09:30", periods=len(prices), freq="5min")
    return pd.DataFrame({"datetime": idx, "open": prices, "high": [p * 1.001 for p in prices], "low": [p * 0.999 for p in prices], "close": prices, "volume": 100000, "amount": [p * 100000 for p in prices]})


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
    assert summarize_trades(paid).trades == 1


def test_hindsight_envelope_uses_no_trade_floor_after_costs():
    bars = make_intraday([10.0, 10.0, 10.0, 10.0])
    assert best_single_t_envelope(bars, "positive", 1000, 0.5, CostModel()).empty
    assert best_single_t_envelope(bars, "reverse", 1000, 0.5, CostModel()).empty


def test_causal_mean_reversion_executes_after_signal():
    prices = [10, 10.1, 10.0, 10.05, 10.0, 9.5, 9.2, 9.4, 9.7, 10.0, 10.1, 10.0]
    costs = CostModel(commission_rate=0, min_commission=0, stamp_duty_sell_rate=0, slippage_bps=0)
    trades = mean_reversion_backtest(make_intraday(prices), "positive", bottom_shares=1000, t_ratio=0.5, costs=costs, window=4, entry_z=1.0)
    assert len(trades) == 1
    assert trades.iloc[0]["strategy"] == "rolling_z_mean_reversion"
    assert trades.iloc[0]["shares"] == 500


def test_linear_best_pair_matches_bruteforce_with_costs():
    from backtest.t_engine import _best_net_pair, _trade_from_pair

    rng = np.random.default_rng(123)
    costs = CostModel(commission_rate=0.00037, min_commission=5.0, stamp_duty_sell_rate=0.0005, slippage_bps=3.0)
    for mode in ["positive", "reverse"]:
        for _ in range(12):
            prices = 10 + np.cumsum(rng.normal(0, 0.25, 16))
            day = pd.DataFrame({"datetime": pd.date_range("2026-01-05 09:30", periods=len(prices), freq="5min"), "close": prices})
            pair = _best_net_pair(day, mode, 500, costs)
            assert pair is not None
            linear = _trade_from_pair(day, mode, pair[0], pair[1], 500, costs, "linear")
            brute = None
            for i in range(len(day) - 1):
                for j in range(i + 1, len(day)):
                    trade = _trade_from_pair(day, mode, i, j, 500, costs, "brute")
                    if brute is None or trade.net_pnl > brute.net_pnl:
                        brute = trade
            assert brute is not None
            assert linear.net_pnl == brute.net_pnl
