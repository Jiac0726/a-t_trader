from __future__ import annotations

import numpy as np
import pandas as pd


def turning_points(series: pd.Series, threshold_pct: float = 1.0) -> list[tuple[pd.Timestamp, float, str]]:
    """Simple zig-zag turning-point detector for 5-minute closes.

    Returns confirmed local pivots after price reverses by threshold_pct.
    This is intentionally transparent and parameterized for later backtesting.
    """
    s = series.dropna()
    if len(s) < 3:
        return []
    threshold = threshold_pct / 100.0
    points: list[tuple[pd.Timestamp, float, str]] = []
    direction = 0
    pivot_idx = s.index[0]
    pivot_price = float(s.iloc[0])

    for idx, value in s.iloc[1:].items():
        price = float(value)
        change = price / pivot_price - 1
        if direction == 0:
            if change >= threshold:
                direction = 1
                points.append((pivot_idx, pivot_price, "low"))
                pivot_idx, pivot_price = idx, price
            elif change <= -threshold:
                direction = -1
                points.append((pivot_idx, pivot_price, "high"))
                pivot_idx, pivot_price = idx, price
        elif direction == 1:
            if price > pivot_price:
                pivot_idx, pivot_price = idx, price
            elif price / pivot_price - 1 <= -threshold:
                points.append((pivot_idx, pivot_price, "high"))
                direction = -1
                pivot_idx, pivot_price = idx, price
        else:
            if price < pivot_price:
                pivot_idx, pivot_price = idx, price
            elif price / pivot_price - 1 >= threshold:
                points.append((pivot_idx, pivot_price, "low"))
                direction = 1
                pivot_idx, pivot_price = idx, price
    return points


def intraday_opportunity_features(df: pd.DataFrame, threshold_pct: float = 1.0) -> dict[str, float]:
    x = df.copy()
    x["date"] = x["datetime"].dt.date
    opportunity_counts = []
    summed_space = []
    low_hours: list[float] = []
    high_hours: list[float] = []

    for _, day in x.groupby("date"):
        day = day.sort_values("datetime")
        pts = turning_points(day.set_index("datetime")["close"], threshold_pct=threshold_pct)
        opportunity_counts.append(max(0, len(pts) - 1))
        space = 0.0
        for (t1, p1, k1), (t2, p2, k2) in zip(pts, pts[1:]):
            if k1 != k2 and p1 > 0:
                space += abs(p2 / p1 - 1) * 100
        summed_space.append(space)
        day_low = day.loc[day["low"].idxmin(), "datetime"]
        day_high = day.loc[day["high"].idxmax(), "datetime"]
        low_hours.append(day_low.hour + day_low.minute / 60)
        high_hours.append(day_high.hour + day_high.minute / 60)

    return {
        "avg_opportunities": float(np.mean(opportunity_counts) if opportunity_counts else 0),
        "avg_effective_t_space": float(np.mean(summed_space) if summed_space else 0),
        "median_low_hour": float(np.median(low_hours) if low_hours else np.nan),
        "median_high_hour": float(np.median(high_hours) if high_hours else np.nan),
        "days": int(len(opportunity_counts)),
    }
