from __future__ import annotations

from datetime import date
import pandas as pd

from providers.base import MarketDataProvider, NoMarketData
from storage.duckdb_store import DuckDBStore


class CachedProvider(MarketDataProvider):
    """DuckDB-backed wrapper around any MarketDataProvider.

    Cache identity includes adjustment mode. For qfq/hfq extension requests the
    wrapper deliberately refetches a small overlapping window. If overlapping
    closes changed, the upstream adjustment scale changed (or the effective
    source changed materially), so the whole cached span for that adjustment
    namespace is rebuilt before new bars are stored.

    This avoids two silent corruption modes:
    1. mixing raw/qfq/hfq rows under the same code+interval cache key;
    2. mixing old qfq scale with newly adjusted rows after a corporate action.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        store: DuckDBStore,
        universe_ttl_hours: float = 6.0,
        adjusted_overlap_days: int = 10,
    ):
        self.provider = provider
        self.store = store
        self.name = f"cached:{provider.name}"
        self.universe_ttl_hours = max(0.0, float(universe_ttl_hours))
        self.adjusted_overlap_days = max(3, int(adjusted_overlap_days))

    @staticmethod
    def _day(value: date | str | pd.Timestamp) -> pd.Timestamp:
        return pd.Timestamp(value).normalize()

    @staticmethod
    def _adjust(adjust: str) -> str:
        return str(adjust or "none")

    @staticmethod
    def _is_adjusted(adjust: str) -> bool:
        return str(adjust or "none").lower() in {"qfq", "hfq"}

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

    # The fallback calls keep lightweight in-memory stores used by old tests and
    # downstream notebooks compatible while the real DuckDB store uses adjust.
    def _history_bounds(self, code: str, interval: str, adjust: str):
        fn = self.store.history_bounds
        try:
            return fn(code, interval, adjust=adjust)
        except TypeError:
            return fn(code, interval)

    def _load_history(self, code: str, interval: str, start=None, end=None, adjust: str = "qfq"):
        fn = self.store.load_history
        try:
            return fn(code, interval, start, end, adjust=adjust)
        except TypeError:
            return fn(code, interval, start, end)

    def _save_history(self, code: str, interval: str, df: pd.DataFrame, adjust: str) -> None:
        fn = self.store.save_history
        try:
            fn(code, interval, df, adjust=adjust)
        except TypeError:
            fn(code, interval, df)

    def _coverage_bounds(self, code: str, interval: str, adjust: str):
        fn = getattr(self.store, "coverage_bounds", None)
        if callable(fn):
            try:
                low, high = fn(code, interval, adjust=adjust)
            except TypeError:
                low, high = fn(code, interval)
            if low is not None and high is not None:
                return low, high
        return self._history_bounds(code, interval, adjust)

    def _mark_coverage(self, code: str, interval: str, start: pd.Timestamp, end: pd.Timestamp, adjust: str) -> None:
        fn = getattr(self.store, "mark_history_coverage", None)
        if callable(fn):
            try:
                fn(code, interval, start, end, adjust=adjust)
            except TypeError:
                fn(code, interval, start, end)

    def _clear_history(self, code: str, interval: str, adjust: str) -> bool:
        fn = getattr(self.store, "clear_history", None)
        if not callable(fn):
            return False
        try:
            fn(code, interval, adjust=adjust)
        except TypeError:
            fn(code, interval)
        return True

    @staticmethod
    def _adjustment_scale_changed(cached: pd.DataFrame, fresh: pd.DataFrame) -> bool:
        if cached is None or fresh is None or cached.empty or fresh.empty:
            return False
        if "datetime" not in cached.columns or "datetime" not in fresh.columns or "close" not in cached.columns or "close" not in fresh.columns:
            return False
        left = cached[["datetime", "close"]].copy()
        right = fresh[["datetime", "close"]].copy()
        left["datetime"] = pd.to_datetime(left["datetime"])
        right["datetime"] = pd.to_datetime(right["datetime"])
        left["close"] = pd.to_numeric(left["close"], errors="coerce")
        right["close"] = pd.to_numeric(right["close"], errors="coerce")
        merged = left.merge(right, on="datetime", suffixes=("_cached", "_fresh")).dropna()
        if merged.empty:
            return False
        diff = (merged["close_cached"] - merged["close_fresh"]).abs()
        scale = merged[["close_cached", "close_fresh"]].abs().max(axis=1).clip(lower=1.0)
        return bool((diff / scale > 1e-6).any())

    def history(self, code: str, start: date | str, end: date | str, interval: str = "1d", adjust: str = "qfq") -> pd.DataFrame:
        adjust = self._adjust(adjust)
        start_ts = self._day(start)
        end_ts = self._day(end)
        coverage_low, coverage_high = self._coverage_bounds(code, interval, adjust)
        row_low, row_high = self._history_bounds(code, interval, adjust)
        fetched_name = ""
        fetched_provider = ""

        ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        adjusted = self._is_adjusted(adjust)
        if coverage_low is None or coverage_high is None:
            ranges.append((start_ts, end_ts))
        else:
            coverage_low = coverage_low.normalize()
            coverage_high = coverage_high.normalize()
            if adjusted and start_ts < coverage_low:
                # Fetch through the existing right edge so a provider that
                # anchors qfq to request-end cannot create a second scale.
                ranges.append((start_ts, max(end_ts, coverage_high)))
            else:
                if start_ts < coverage_low:
                    ranges.append((start_ts, coverage_low - pd.Timedelta(days=1)))
                if end_ts > coverage_high:
                    if adjusted:
                        overlap_left = max(coverage_low, coverage_high - pd.Timedelta(days=self.adjusted_overlap_days))
                        ranges.append((overlap_left, end_ts))
                    else:
                        ranges.append((coverage_high + pd.Timedelta(days=1), end_ts))

        for left, right in ranges:
            if left > right:
                continue
            cached_overlap = pd.DataFrame()
            if adjusted and coverage_low is not None and coverage_high is not None:
                overlap_left = max(left, coverage_low)
                overlap_right = min(right, coverage_high)
                if overlap_left <= overlap_right:
                    cached_overlap = self._load_history(code, interval, overlap_left, overlap_right, adjust=adjust)

            try:
                fresh = self.provider.history(code, left.date(), right.date(), interval=interval, adjust=adjust)
            except NoMarketData:
                if row_low is None or row_high is None:
                    raise
                self._mark_coverage(code, interval, left, right, adjust)
                continue

            fetched_name = fresh.attrs.get("name", fetched_name)
            fetched_provider = fresh.attrs.get("provider", self.provider.name)

            if adjusted and not cached_overlap.empty and self._adjustment_scale_changed(cached_overlap, fresh):
                # Corporate-action scale changed. Rebuild the full known span so
                # no old-scale qfq rows survive next to new-scale rows.
                rebuild_left = min(start_ts, coverage_low) if coverage_low is not None else start_ts
                rebuild_right = max(end_ts, coverage_high) if coverage_high is not None else end_ts
                rebuilt = self.provider.history(
                    code,
                    rebuild_left.date(),
                    rebuild_right.date(),
                    interval=interval,
                    adjust=adjust,
                )
                fetched_name = rebuilt.attrs.get("name", fetched_name)
                fetched_provider = rebuilt.attrs.get("provider", fetched_provider or self.provider.name)
                if self._clear_history(code, interval, adjust):
                    self._save_history(code, interval, rebuilt, adjust)
                    self._mark_coverage(code, interval, rebuild_left, rebuild_right, adjust)
                    break
                # Legacy/in-memory stores without a clear operation can still
                # overwrite the fetched span; production DuckDB always clears.
                self._save_history(code, interval, rebuilt, adjust)
                self._mark_coverage(code, interval, rebuild_left, rebuild_right, adjust)
                break

            self._save_history(code, interval, fresh, adjust)
            self._mark_coverage(code, interval, left, right, adjust)

        out = self._load_history(code, interval, start_ts, end_ts, adjust=adjust)
        if not fetched_name:
            stocks = self.store.load_stock_list()
            if not stocks.empty and "code" in stocks.columns and "name" in stocks.columns:
                match = stocks[stocks["code"].astype(str).str.zfill(6) == str(code).zfill(6)]
                if not match.empty:
                    fetched_name = str(match.iloc[0]["name"])
        out.attrs["name"] = fetched_name
        out.attrs["provider"] = fetched_provider or self.name
        out.attrs["adjust"] = adjust
        return out
