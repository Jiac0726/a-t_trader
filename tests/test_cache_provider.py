from __future__ import annotations

from datetime import date
import pandas as pd

from data.cached_provider import CachedProvider
from providers.demo import DemoProvider


class MemoryStore:
    def __init__(self):
        self.histories: dict[tuple[str, str], pd.DataFrame] = {}
        self.stocks = pd.DataFrame()

    def load_stock_list(self):
        return self.stocks.copy()

    def save_stock_list(self, stocks, provider=""):
        self.stocks = stocks.copy()
        self.stocks["provider"] = provider

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

    def load_history(self, code, interval, start=None, end=None):
        df = self.histories.get((code, interval), pd.DataFrame()).copy()
        if df.empty:
            return df
        if start is not None:
            df = df[df["datetime"] >= pd.Timestamp(start)]
        if end is not None:
            end_ts = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            df = df[df["datetime"] <= end_ts]
        return df.reset_index(drop=True)


class CountingDemo(DemoProvider):
    def __init__(self):
        self.history_calls = 0
        self.list_calls = 0

    def history(self, *args, **kwargs):
        self.history_calls += 1
        return super().history(*args, **kwargs)

    def stock_list(self):
        self.list_calls += 1
        return super().stock_list()


def test_cached_provider_reuses_history_and_universe():
    raw = CountingDemo()
    cached = CachedProvider(raw, MemoryStore())

    first_universe = cached.stock_list()
    second_universe = cached.stock_list()
    assert len(first_universe) == len(second_universe) == 6
    assert raw.list_calls == 1

    start = date(2026, 5, 1)
    end = date(2026, 8, 7)
    first = cached.history("300059", start, end)
    second = cached.history("300059", start, end)
    assert len(first) == len(second)
    assert second.attrs["name"] == "DEMO-300059"
    assert raw.history_calls == 1


def test_cached_provider_reuses_5m_history():
    raw = CountingDemo()
    cached = CachedProvider(raw, MemoryStore())
    start = date(2026, 8, 3)
    end = date(2026, 8, 7)
    first = cached.history("300059", start, end, interval="5m")
    second = cached.history("300059", start, end, interval="5m")
    assert len(first) == len(second)
    assert len(first) > 20
    assert raw.history_calls == 1
