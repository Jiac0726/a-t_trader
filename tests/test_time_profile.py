from __future__ import annotations

import pandas as pd

from features.time_profile import daily_extreme_times, extreme_time_distribution, summarize_extreme_times


def make_bars() -> pd.DataFrame:
    rows = []
    specs = {
        "2026-08-03": [("09:30", 10.0), ("10:00", 9.0), ("10:30", 11.0), ("14:30", 10.0)],
        "2026-08-04": [("09:30", 10.0), ("10:00", 11.0), ("13:30", 10.5), ("14:30", 9.0)],
        "2026-08-05": [("09:30", 9.0), ("10:00", 10.0), ("13:30", 11.0), ("14:30", 10.0)],
    }
    for day, bars in specs.items():
        for clock, price in bars:
            rows.append(
                {
                    "datetime": pd.Timestamp(f"{day} {clock}"),
                    "open": price,
                    "high": price + 0.1,
                    "low": price - 0.1,
                    "close": price,
                    "volume": 100,
                    "amount": 1000,
                }
            )
    return pd.DataFrame(rows)


def test_daily_extreme_times_uses_earliest_tie():
    bars = make_bars()
    daily = daily_extreme_times(bars)
    assert len(daily) == 3
    assert daily.iloc[0]["low_time"].strftime("%H:%M") == "10:00"
    assert daily.iloc[0]["high_time"].strftime("%H:%M") == "10:30"


def test_extreme_distribution_percentages_sum_to_100_per_kind():
    dist = extreme_time_distribution(make_bars(), bucket_minutes=30)
    totals = dist.groupby("kind")["pct"].sum().round(1).to_dict()
    assert totals == {"high": 100.0, "low": 100.0}


def test_extreme_summary_rates_and_medians():
    summary = summarize_extreme_times(make_bars())
    assert summary.days == 3
    assert summary.median_low_time == "10:00"
    assert summary.median_high_time == "10:30"
    assert round(summary.last_hour_low_rate, 2) == 33.33
    assert round(summary.last_hour_high_rate, 2) == 0.0
