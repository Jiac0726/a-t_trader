from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(slots=True)
class ExtremeTimeSummary:
    days: int
    median_low_time: str
    median_high_time: str
    morning_low_rate: float
    morning_high_rate: float
    last_hour_low_rate: float
    last_hour_high_rate: float

    def to_dict(self) -> dict:
        return asdict(self)


def _clock_minutes(ts: pd.Timestamp) -> int:
    return int(ts.hour * 60 + ts.minute)


def _clock_label(minutes: float) -> str:
    if not np.isfinite(minutes):
        return ""
    value = int(round(minutes))
    return f"{value // 60:02d}:{value % 60:02d}"


def _bucket_label(ts: pd.Timestamp, bucket_minutes: int) -> str:
    minutes = _clock_minutes(ts)
    start = (minutes // bucket_minutes) * bucket_minutes
    end = start + bucket_minutes
    return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"


def daily_extreme_times(df: pd.DataFrame) -> pd.DataFrame:
    """Return one daily high-time and low-time row from intraday OHLC bars.

    Ties use the earliest bar of the day. The function only summarizes observed
    bars and does not infer a continuous intrabar turning point.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "low_time", "high_time", "low_price", "high_price"])
    required = {"datetime", "high", "low"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    x = df.copy()
    x["datetime"] = pd.to_datetime(x["datetime"], errors="coerce")
    x["high"] = pd.to_numeric(x["high"], errors="coerce")
    x["low"] = pd.to_numeric(x["low"], errors="coerce")
    x = x.dropna(subset=["datetime", "high", "low"]).sort_values("datetime")
    x["date"] = x["datetime"].dt.date

    rows: list[dict] = []
    for day_value, day in x.groupby("date", sort=True):
        if day.empty:
            continue
        low_value = day["low"].min()
        high_value = day["high"].max()
        low_row = day[day["low"] == low_value].iloc[0]
        high_row = day[day["high"] == high_value].iloc[0]
        rows.append(
            {
                "date": str(day_value),
                "low_time": pd.Timestamp(low_row["datetime"]),
                "high_time": pd.Timestamp(high_row["datetime"]),
                "low_price": float(low_value),
                "high_price": float(high_value),
            }
        )
    return pd.DataFrame(rows)


def extreme_time_distribution(df: pd.DataFrame, bucket_minutes: int = 30) -> pd.DataFrame:
    """Build percentage distribution of daily high/low occurrence times."""
    if bucket_minutes <= 0 or 60 % bucket_minutes != 0:
        raise ValueError("bucket_minutes must be a positive divisor of 60")
    daily = daily_extreme_times(df)
    if daily.empty:
        return pd.DataFrame(columns=["kind", "bucket", "count", "pct"])

    rows: list[dict] = []
    for kind, col in [("low", "low_time"), ("high", "high_time")]:
        buckets = daily[col].map(lambda ts: _bucket_label(pd.Timestamp(ts), bucket_minutes))
        counts = buckets.value_counts().sort_index()
        total = int(counts.sum())
        for bucket, count in counts.items():
            rows.append(
                {
                    "kind": kind,
                    "bucket": bucket,
                    "count": int(count),
                    "pct": round(float(count / total * 100), 2) if total else 0.0,
                }
            )
    return pd.DataFrame(rows).sort_values(["kind", "bucket"]).reset_index(drop=True)


def summarize_extreme_times(df: pd.DataFrame) -> ExtremeTimeSummary:
    daily = daily_extreme_times(df)
    if daily.empty:
        return ExtremeTimeSummary(0, "", "", 0.0, 0.0, 0.0, 0.0)

    lows = daily["low_time"].map(lambda ts: _clock_minutes(pd.Timestamp(ts))).astype(float)
    highs = daily["high_time"].map(lambda ts: _clock_minutes(pd.Timestamp(ts))).astype(float)
    days = len(daily)
    morning_cutoff = 11 * 60 + 30
    last_hour_start = 14 * 60

    return ExtremeTimeSummary(
        days=days,
        median_low_time=_clock_label(float(np.median(lows))),
        median_high_time=_clock_label(float(np.median(highs))),
        morning_low_rate=round(float((lows <= morning_cutoff).mean() * 100), 2),
        morning_high_rate=round(float((highs <= morning_cutoff).mean() * 100), 2),
        last_hour_low_rate=round(float((lows >= last_hour_start).mean() * 100), 2),
        last_hour_high_rate=round(float((highs >= last_hour_start).mean() * 100), 2),
    )
