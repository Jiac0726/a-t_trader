from __future__ import annotations

import numpy as np
import pandas as pd


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def max_drawdown(close: pd.Series) -> float:
    running_max = close.cummax()
    dd = close / running_max - 1.0
    return float(dd.min() * 100)


def daily_features(df: pd.DataFrame, lookback: int = 60) -> dict[str, float]:
    x = df.tail(lookback).copy()
    prev_close = x["close"].shift(1)
    amplitude = x.get("amplitude")
    if amplitude is None:
        amplitude = (x["high"] - x["low"]) / prev_close * 100
    returns = x["close"].pct_change()
    intraday_space = (x["high"] - x["low"]) / x["open"].replace(0, np.nan) * 100
    tr = _true_range(x)
    atr_pct = (tr.rolling(14).mean() / x["close"] * 100).iloc[-1]
    ma20 = x["close"].rolling(20).mean()
    trend_distance = ((x["close"] / ma20 - 1).abs() * 100).tail(20).mean()
    reversal_rate = ((returns * returns.shift(1)) < 0).tail(lookback - 2).mean() * 100
    extreme_days = (amplitude > 10).mean() * 100

    return {
        "avg_amplitude": float(amplitude.mean()),
        "median_amount": float(x["amount"].median()),
        "avg_intraday_space": float(intraday_space.mean()),
        "return_volatility": float(returns.std(ddof=0) * np.sqrt(252) * 100),
        "atr_pct": float(atr_pct if pd.notna(atr_pct) else 0),
        "trend_distance": float(trend_distance if pd.notna(trend_distance) else 0),
        "reversal_rate": float(reversal_rate if pd.notna(reversal_rate) else 0),
        "max_drawdown": max_drawdown(x["close"]),
        "extreme_day_rate": float(extreme_days),
        "latest_close": float(x["close"].iloc[-1]),
        "observations": int(len(x)),
    }
