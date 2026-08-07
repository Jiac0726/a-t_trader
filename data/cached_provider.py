from __future__ import annotations

from datetime import date
import pandas as pd

from providers.base import MarketDataProvider
from storage.duckdb_store import DuckDBStore


class CachedProvider(MarketDataProvider):
    """DuckDB-backed wrapper around any MarketDataProvider.

    It fetches only missing date ranges, then serves the normalized combined
    history from the local cache. This keeps scanner logic independent from the
    upstream data source.
    """

    def __init__(self, provider: MarketDataProvider, store: DuckDBStore):
        self.provider = provider
        self.store = store
        self.name = f"cached:{provider.name}"

    @staticmethod
    def _day(value: date | str | pd.Timestamp) -> pd.Timestamp:
        return pd.Timestamp(value).normalize()

    def stock_list(self) -> pd.DataFrame:
        cached = self.store.load_stock_list()
        if not cached.empty:
            return cached
        stocks = self.provider.stock_list()
        self.store.save_stock_list(stocks, provider=stocks.attrs.get("provider", self.provider.name))
        return self.store.load_stock_list()

    def refresh_stock_list(self) -> pd.DataFrame:
        stocks = self.provider.stock_list()
        self.store.save_stock_list(stocks, provider=stocks.attrs.get("provider", self.provider.name))
        return self.store.load_stock_list()

    def history(self, code: str, start: date | str, end: date | str, interval: str = "1d", adjust: str = "qfq") -> pd.DataFrame:
        start_ts = self._day(start)
        end_ts = self._day(end)
        low, high = self.store.history_bounds(code, interval)
        fetched_name = ""
        fetched_provider = ""

        ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        if low is None or high is None:
            ranges.append((start_ts, end_ts))
        else:
            low = low.normalize()
            high = high.normalize()
            if start_ts < low:
                ranges.append((start_ts, low - pd.Timedelta(days=1)))
            if end_ts > high:
                ranges.append((high + pd.Timedelta(days=1), end_ts))

        for left, right in ranges:
            if left > right:
                continue
            fresh = self.provider.history(code, left.date(), right.date(), interval=interval, adjust=adjust)
            fetched_name = fresh.attrs.get("name", fetched_name)
            fetched_provider = fresh.attrs.get("provider", self.provider.name)
            self.store.save_history(code, interval, fresh)

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
