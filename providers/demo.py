from __future__ import annotations

from datetime import date
import numpy as np
import pandas as pd

from .base import MarketDataProvider, NoMarketData


class DemoProvider(MarketDataProvider):
    """Deterministic offline data for testing UI and scoring logic."""

    name = "demo"

    def stock_list(self) -> pd.DataFrame:
        codes = ["300059", "601899", "601138", "000063", "300750", "300308"]
        return pd.DataFrame(
            {
                "code": codes,
                "name": [f"DEMO-{c}" for c in codes],
                "market": ["SZ", "SH", "SH", "SZ", "SZ", "SZ"],
            }
        )

    def history(self, code: str, start: date | str, end: date | str, interval: str = "1d", adjust: str = "qfq") -> pd.DataFrame:
        code = str(code).zfill(6)
        seed = int(code[-4:]) + (0 if interval == "1d" else 10000)
        rng = np.random.default_rng(seed)
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if interval == "1d":
            idx = pd.bdate_range(start_ts, end_ts)
            n = len(idx)
            base = 20 + (int(code[-2:]) % 30)
            returns = rng.normal(0.0004, 0.018, n)
            close = base * np.cumprod(1 + returns)
            open_ = close * (1 + rng.normal(0, 0.006, n))
            spread = np.clip(rng.normal(0.035, 0.012, n), 0.008, 0.09)
            high = np.maximum(open_, close) * (1 + spread / 2)
            low = np.minimum(open_, close) * (1 - spread / 2)
            volume = rng.integers(1_000_000, 30_000_000, n)
            amount = volume * close
        else:
            days = pd.bdate_range(start_ts, end_ts)
            stamps = []
            for day in days:
                stamps.extend(pd.date_range(day + pd.Timedelta(hours=9, minutes=30), day + pd.Timedelta(hours=11, minutes=30), freq="5min", inclusive="left"))
                stamps.extend(pd.date_range(day + pd.Timedelta(hours=13), day + pd.Timedelta(hours=15), freq="5min", inclusive="left"))
            idx = pd.DatetimeIndex(stamps)
            n = len(idx)
            base = 20 + (int(code[-2:]) % 30)
            returns = rng.normal(0, 0.0035, n)
            close = base * np.cumprod(1 + returns)
            open_ = np.r_[close[0], close[:-1]]
            high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0025, n))
            low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0025, n))
            volume = rng.integers(20_000, 500_000, n)
            amount = volume * close
        df = pd.DataFrame({"datetime": idx, "open": open_, "high": high, "low": low, "close": close, "volume": volume, "amount": amount})
        if df.empty:
            raise NoMarketData(f"Demo range has no market rows for {code}")
        prev = df["close"].shift(1)
        df["amplitude"] = (df["high"] - df["low"]) / prev * 100
        df["pct_change"] = df["close"].pct_change() * 100
        df.attrs["name"] = f"DEMO-{code}"
        df.attrs["provider"] = self.name
        return df
