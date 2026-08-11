from __future__ import annotations

from datetime import date
import pandas as pd

from data.hydrator import hydrate_codes
from providers.demo import DemoProvider


class MemoryHistoryStore:
    def __init__(self):
        self.histories: dict[tuple[str, str], pd.DataFrame] = {}

    def history_bounds(self, code, interval):
        df = self.histories.get((code, interval))
        if df is None or df.empty:
            return None, None
        return pd.Timestamp(df["datetime"].min()), pd.Timestamp(df["datetime"].max())

    def save_history(self, code, interval, df):
        key = (code, interval)
        current = self.histories.get(key, pd.DataFrame())
        merged = pd.concat([current, df], ignore_index=True)
        merged["datetime"] = pd.to_datetime(merged["datetime"])
        self.histories[key] = merged.drop_duplicates("datetime", keep="last").sort_values("datetime").reset_index(drop=True)


def test_parallel_hydration_only_fetches_missing_ranges():
    store = MemoryHistoryStore()
    codes = ["300059", "601899"]
    first = hydrate_codes(
        codes,
        provider_factory=DemoProvider,
        store=store,
        start=date(2026, 5, 1),
        end=date(2026, 8, 7),
        workers=2,
        requests_per_second=0,
        retries=0,
    )
    assert first.requested_ranges == 2
    assert first.succeeded_ranges == 2
    assert first.failed_ranges == 0
    assert first.fetched_rows > 0

    second = hydrate_codes(
        codes,
        provider_factory=DemoProvider,
        store=store,
        start=date(2026, 5, 1),
        end=date(2026, 8, 7),
        workers=2,
        requests_per_second=0,
        retries=0,
    )
    assert second.requested_ranges == 0
    assert second.fetched_rows == 0
