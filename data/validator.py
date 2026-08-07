from __future__ import annotations

import pandas as pd

REQUIRED = ["datetime", "open", "high", "low", "close", "volume", "amount"]


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Empty market data")
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    out = df.copy()
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    for c in REQUIRED[1:]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["datetime", "open", "high", "low", "close"])
    out = out[(out["high"] >= out[["open", "close", "low"]].max(axis=1)) & (out["low"] <= out[["open", "close", "high"]].min(axis=1))]
    out = out.sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)
    if len(out) < 20:
        raise ValueError(f"Not enough observations: {len(out)}")
    return out
