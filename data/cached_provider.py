from __future__ import annotations

from datetime import date
import pandas as pd

from providers.base import MarketDataProvider, NoMarketData
from storage.duckdb_store import DuckDBStore


class CachedProvider(MarketDataProvider):
    """DuckDB-backed wrapper around any MarketDataProvider.

    Cache coverage is tracked separately from actual K-line row bounds. This
    matters on weekends, holidays and suspension days: a date range may have
    been checked successfully even when no row exists for its final calendar day.
    """

    def __init__(self, provider: MarketDataProvider, store: DuckDBStore, universe_ttl_hours: float = 6.0):
        self.provider = provider
        self.store = store
        self.name = f"cached:{provider.name}"
        self.universe_ttl_hours = max(0.0, float(universe_ttl_hours))

    @staticmethod
    def _day(value: date | str | pd.Timestamp) -> pd.Timestamp:
        return pd.Timestamp(value).normalize()

    def stock_list(self) -> pd.DataFrame:
        cached = self.store.load_stock_list()
        age_fn = getattr(self.store, "stock_list_age_hours", None)
        age_hours = age_fn() if callable(age_fn) else None
        cache_fresh = not cached.empty and (age_hours is None or age_hours <= self.universe_ttl_hours)
        if cache_fresh:
            return cached
        return self.refresh_stock_list()

    def refresh_stock_list(self) -> pd.DataFrame:
        stocks = self.provider.stock_list()
        self.store.save_stock_list(stocks, provider=stocks.attrs.get("provider", self.provider.name))
        return self.store.load_stock_list()

    def _coverage_bounds(self, code: str, interval: str):
        fn = getattr(self.store, "coverage_bounds", None)
        if callable(fn):
            low, high = fn(code, interval)
            if low is not None and high is not None:
                return low, high
        return self.store.history_bounds(code, interval)

    def _mark_coverage(self, code: str, interval: str, start: pd.Timestamp, end: pd.Timestamp) -> None:
        fn = getattr(self.store, "mark_history_coverage", None)
        if callable(fn):
            fn(code, interval, start, end)

    def history(self, code: str, start: date | str, end: date | str, interval: str = "1d", adjust: str = "qfq") -> pd.DataFrame:
        start_ts = self._day(start)
        end_ts = self._day(end)
        coverage_low, coverage_high = self._coverage_bounds(code, interval)
        row_low, row_high = self.store.history_bounds(code, interval)
        fetched_name = ""
        fetched_provider = ""

        ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        if coverage_low is None or coverage_high is None:
            ranges.append((start_ts, end_ts))
        else:
            coverage_low = coverage_low.normalize()
            coverage_high = coverage_high.normalize()
            if start_ts < coverage_low:
                ranges.append((start_ts, coverage_low - pd.Timedelta(days=1)))
            if end_ts > coverage_high:
                ranges.append((coverage_high + pd.Timedelta(days=1), end_ts))

        for left, right in ranges:
            if left > right:
                continue
            try:
                fresh = self.provider.history(code, left.date(), right.date(), interval=interval, adjust=adjust)
            except NoMarketData:
                if row_low is None or row_high is None:
                    raise
                self._mark_coverage(code, interval, left, right)
                continue
            fetched_name = fresh.attrs.get("name", fetched_name)
            fetched_provider = fresh.attrs.get("provider", self.provider.name)
            self.store.save_history(code, interval, fresh)
            self._mark_coverage(code, interval, left, right)

        out = self.store.load_history(code, interval, start_ts, end_ts)
        if not fetched_name:
            stocks = self.store.load_stock_list()
            if not stocks.empty and "code" in stocks.columns and "name" in stocks.columns:
                match = stocks[stocks["code"].astype(str).str.zfill(6) == str(code).zfill(6)]
                if not match.empty:
                    fetched_name = str(match.iloc[0]["name"])
        out.attrs["name"] = fetched_name
        out.attrs["provider"] = fetched_provider or self.name
        return out
