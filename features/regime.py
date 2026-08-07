from __future__ import annotations

import numpy as np
import pandas as pd

REGIME_ORDER = ("trend_up", "trend_down", "range", "high_vol")


def _rolling_current_percentile(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    def current_pct(values: np.ndarray) -> float:
        values = values[np.isfinite(values)]
        if len(values) == 0:
            return np.nan
        current = values[-1]
        return float(np.mean(values <= current))
    return series.rolling(window=max(2, int(window)), min_periods=max(2, int(min_periods))).apply(current_pct, raw=True)


def classify_price_regime(
    daily_df: pd.DataFrame,
    *,
    trend_window: int = 20,
    vol_window: int = 20,
    vol_rank_window: int = 120,
    vol_rank_min_periods: int = 40,
    efficiency_threshold: float = 0.35,
    min_trend_return_pct: float = 2.0,
    high_vol_percentile: float = 0.80,
    high_vol_floor_pct: float = 25.0,
) -> pd.DataFrame:
    """Causal trailing price-regime labels with no fitted model or look-ahead."""
    if daily_df is None or daily_df.empty:
        return pd.DataFrame(columns=["date", "regime", "trend_return_pct", "efficiency_ratio", "realized_vol_pct", "vol_percentile"])
    missing = {"datetime", "close"} - set(daily_df.columns)
    if missing:
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    trend_window = max(2, int(trend_window))
    vol_window = max(2, int(vol_window))
    x = daily_df[["datetime", "close"]].copy().sort_values("datetime").reset_index(drop=True)
    x["datetime"] = pd.to_datetime(x["datetime"], errors="coerce")
    x["date"] = x["datetime"].dt.normalize()
    close = pd.to_numeric(x["close"], errors="coerce")
    log_ret = np.log(close / close.shift(1))
    x["trend_return_pct"] = close.pct_change(trend_window, fill_method=None) * 100.0
    path = close.diff().abs().rolling(trend_window, min_periods=trend_window).sum()
    displacement = (close - close.shift(trend_window)).abs()
    x["efficiency_ratio"] = (displacement / path.replace(0, np.nan)).clip(0, 1)
    x["realized_vol_pct"] = log_ret.rolling(vol_window, min_periods=vol_window).std(ddof=0) * np.sqrt(252.0) * 100.0
    x["vol_percentile"] = _rolling_current_percentile(x["realized_vol_pct"], vol_rank_window, vol_rank_min_periods)

    ready = x[["trend_return_pct", "efficiency_ratio", "realized_vol_pct", "vol_percentile"]].notna().all(axis=1)
    regime = pd.Series(pd.NA, index=x.index, dtype="object")
    high_vol = ready & (x["vol_percentile"] >= float(high_vol_percentile)) & (x["realized_vol_pct"] >= float(high_vol_floor_pct))
    trending = ready & ~high_vol & (x["efficiency_ratio"] >= float(efficiency_threshold)) & (x["trend_return_pct"].abs() >= float(min_trend_return_pct))
    regime.loc[high_vol] = "high_vol"
    regime.loc[trending & (x["trend_return_pct"] > 0)] = "trend_up"
    regime.loc[trending & (x["trend_return_pct"] < 0)] = "trend_down"
    regime.loc[ready & regime.isna()] = "range"
    x["regime"] = regime
    return x[["date", "regime", "trend_return_pct", "efficiency_ratio", "realized_vol_pct", "vol_percentile"]]


def attach_market_regime(panel: pd.DataFrame, regime_history: pd.DataFrame, *, output_col: str = "market_regime", unknown: str = "drop") -> pd.DataFrame:
    if unknown not in {"drop", "keep"}:
        raise ValueError("unknown must be 'drop' or 'keep'")
    if panel is None or panel.empty:
        return panel.copy() if isinstance(panel, pd.DataFrame) else pd.DataFrame()
    if regime_history is None or regime_history.empty:
        return panel.copy() if unknown == "keep" else panel.iloc[0:0].copy()
    x = panel.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    r = regime_history[["date", "regime"]].copy()
    r["date"] = pd.to_datetime(r["date"], errors="coerce").dt.normalize()
    r = r.dropna(subset=["date"]).drop_duplicates("date", keep="last").rename(columns={"regime": output_col})
    out = x.merge(r, on="date", how="left", validate="many_to_one")
    if unknown == "drop":
        out = out.dropna(subset=[output_col])
    return out.sort_values(["date"] + (["code"] if "code" in out.columns else [])).reset_index(drop=True)
