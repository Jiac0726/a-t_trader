from __future__ import annotations

from datetime import date
import pandas as pd

from data.hydrator import hydrate_codes
from providers.demo import DemoProvider


class CoverageStore:
    def __init__(self):
        self.histories: dict[tuple[str, str], pd.DataFrame] = {}
        self.coverage: dict[tuple[str, str], tuple[pd.Timestamp, pd.Timestamp]] = {}

    def history_bounds(self, code, interval):
        df = self.histories.get((code, interval))
        if df is None or df.empty:
            return None, None
        return pd.Timestamp(df["datetime"].min()), pd.Timestamp(df["datetime"].max())

    def coverage_bounds(self, code, interval):
        return self.coverage.get((code, interval), (None, None))

    def mark_history_coverage(self, code, interval, start, end):
        key = (code, interval)
        left, right = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        old = self.coverage.get(key)
        if old:
            left = min(left, old[0])
            right = max(right, old[1])
        self.coverage[key] = (left, right)

    def save_history(self, code, interval, df):
        key = (code, interval)
        current = self.histories.get(key, pd.DataFrame())
        merged = pd.concat([current, df], ignore_index=True)
        merged["datetime"] = pd.to_datetime(merged["datetime"])
        self.histories[key] = merged.drop_duplicates("datetime", keep="last").sort_values("datetime").reset_index(drop=True)


def test_coverage_prevents_weekend_refetch():
    store = CoverageStore()
    first = hydrate_codes(
        ["300059"],
        provider_factory=DemoProvider,
        store=store,
        start=date(2026, 8, 7),
        end=date(2026, 8, 9),
        workers=1,
        requests_per_second=0,
        retries=0,
    )
    assert first.succeeded_ranges == 1
    assert store.coverage_bounds("300059", "1d")[1] == pd.Timestamp("2026-08-09")

    second = hydrate_codes(
        ["300059"],
        provider_factory=DemoProvider,
        store=store,
        start=date(2026, 8, 7),
        end=date(2026, 8, 9),
        workers=1,
        requests_per_second=0,
        retries=0,
    )
    assert second.requested_ranges == 0


class TruncatedMinuteProvider(DemoProvider):
    def history(self, code, start, end, interval="1d", adjust="qfq"):
        if interval == "5m":
            # Simulate a public provider that accepts a long request but only
            # returns its recent retention window.
            start = max(pd.Timestamp(start), pd.Timestamp(end) - pd.Timedelta(days=5))
        return super().history(code, start, end, interval=interval, adjust=adjust)


def test_minute_coverage_only_marks_observed_return_window():
    store = CoverageStore()
    report = hydrate_codes(
        ["300059"],
        provider_factory=TruncatedMinuteProvider,
        store=store,
        start=date(2026, 6, 1),
        end=date(2026, 8, 7),
        interval="5m",
        workers=1,
        requests_per_second=0,
        retries=0,
    )
    assert report.succeeded_ranges == 1
    low, high = store.coverage_bounds("300059", "5m")
    assert low >= pd.Timestamp("2026-08-02")
    assert high <= pd.Timestamp("2026-08-07")
    assert low != pd.Timestamp("2026-06-01")
