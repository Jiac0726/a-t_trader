from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
import threading
import time
from typing import Callable

import pandas as pd

from providers.base import MarketDataProvider, NoMarketData
from storage.duckdb_store import DuckDBStore


@dataclass(slots=True)
class HydrationFailure:
    code: str
    start: str
    end: str
    error: str


@dataclass(slots=True)
class HydrationReport:
    codes: int
    requested_ranges: int
    succeeded_ranges: int
    failed_ranges: int
    fetched_rows: int
    elapsed_seconds: float
    failures: list[HydrationFailure]

    def to_dict(self) -> dict:
        return asdict(self)


class GlobalRateLimiter:
    """Simple process-local aggregate rate limiter shared by worker threads."""

    def __init__(self, requests_per_second: float):
        self.interval = 0.0 if requests_per_second <= 0 else 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                time.sleep(delay)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self.interval


def _normalize_day(value: date | str | pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def _is_adjusted(adjust: str) -> bool:
    return str(adjust or "none").lower() in {"qfq", "hfq"}


def _coverage_bounds(store, code: str, interval: str, adjust: str):
    coverage_fn = getattr(store, "coverage_bounds", None)
    if callable(coverage_fn):
        try:
            return coverage_fn(code, interval, adjust=adjust)
        except TypeError:
            return coverage_fn(code, interval)
    bounds_fn = store.history_bounds
    try:
        return bounds_fn(code, interval, adjust=adjust)
    except TypeError:
        return bounds_fn(code, interval)


def _load_history(store, code: str, interval: str, start, end, adjust: str):
    try:
        return store.load_history(code, interval, start, end, adjust=adjust)
    except TypeError:
        return store.load_history(code, interval, start, end)


def _save_history(store, code: str, interval: str, df: pd.DataFrame, adjust: str) -> None:
    try:
        store.save_history(code, interval, df, adjust=adjust)
    except TypeError:
        store.save_history(code, interval, df)


def _clear_history(store, code: str, interval: str, adjust: str) -> bool:
    fn = getattr(store, "clear_history", None)
    if not callable(fn):
        return False
    try:
        fn(code, interval, adjust=adjust)
    except TypeError:
        fn(code, interval)
    return True


def _mark_coverage(store, code: str, interval: str, start, end, adjust: str) -> None:
    mark_fn = getattr(store, "mark_history_coverage", None)
    if not callable(mark_fn):
        return
    try:
        mark_fn(code, interval, start, end, adjust=adjust)
    except TypeError:
        mark_fn(code, interval, start, end)


def _adjustment_scale_changed(cached: pd.DataFrame, fresh: pd.DataFrame) -> bool:
    if cached is None or fresh is None or cached.empty or fresh.empty:
        return False
    if not {"datetime", "close"}.issubset(cached.columns) or not {"datetime", "close"}.issubset(fresh.columns):
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


def plan_missing_ranges(
    codes: list[str],
    store: DuckDBStore,
    start: date | str | pd.Timestamp,
    end: date | str | pd.Timestamp,
    interval: str = "1d",
    adjust: str = "qfq",
    adjusted_overlap_days: int = 10,
) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """Plan fetch work; adjusted tails intentionally include a small overlap."""
    start_ts = _normalize_day(start)
    end_ts = _normalize_day(end)
    overlap_days = max(3, int(adjusted_overlap_days))
    adjusted = _is_adjusted(adjust)
    tasks: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
    for raw_code in codes:
        code = str(raw_code).zfill(6)
        low, high = _coverage_bounds(store, code, interval, adjust)
        if low is None or high is None:
            tasks.append((code, start_ts, end_ts))
            continue
        low = low.normalize()
        high = high.normalize()
        if adjusted and start_ts < low:
            # New left history is fetched through the existing right edge so
            # request-end-anchored qfq providers cannot create another scale.
            tasks.append((code, start_ts, max(end_ts, high)))
        else:
            if start_ts < low:
                tasks.append((code, start_ts, low - pd.Timedelta(days=1)))
            if end_ts > high:
                if adjusted:
                    overlap_left = max(low, high - pd.Timedelta(days=overlap_days))
                    tasks.append((code, overlap_left, end_ts))
                else:
                    tasks.append((code, high + pd.Timedelta(days=1), end_ts))
    return [task for task in tasks if task[1] <= task[2]]


def _fetch_task(
    provider_factory: Callable[[], MarketDataProvider],
    limiter: GlobalRateLimiter,
    code: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    interval: str,
    adjust: str,
    retries: int,
    base_delay: float,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            limiter.wait()
            provider = provider_factory()
            return provider.history(code, start.date(), end.date(), interval=interval, adjust=adjust)
        except NoMarketData:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(base_delay * (2**attempt))
    assert last_error is not None
    raise last_error


def hydrate_codes(
    codes: list[str],
    provider_factory: Callable[[], MarketDataProvider],
    store: DuckDBStore,
    start: date | str | pd.Timestamp,
    end: date | str | pd.Timestamp,
    interval: str = "1d",
    adjust: str = "qfq",
    workers: int = 4,
    requests_per_second: float = 4.0,
    retries: int = 2,
    base_delay: float = 0.5,
    adjusted_overlap_days: int = 10,
) -> HydrationReport:
    """Fetch missing ranges concurrently and serialize cache writes.

    qfq/hfq extension work includes a small overlap. When overlap closes differ,
    the affected symbol's full known adjusted span is refetched and the old
    namespace is cleared before insertion. This gives the parallel all-market
    hydrator the same corporate-action safety semantics as ``CachedProvider``.

    Long ``NoMarketData`` ranges fail closed; only <=4-day daily gaps may be
    marked covered automatically for weekend/short-holiday behavior.
    """

    adjust = str(adjust or "none")
    start_ts = _normalize_day(start)
    end_ts = _normalize_day(end)
    normalized_codes = list(dict.fromkeys(str(c).zfill(6) for c in codes if str(c).strip()))
    tasks = plan_missing_ranges(
        normalized_codes,
        store,
        start_ts,
        end_ts,
        interval=interval,
        adjust=adjust,
        adjusted_overlap_days=adjusted_overlap_days,
    )
    # Snapshot pre-fetch coverage so scale checks compare against the old cache,
    # not against rows written by another task later in this same run.
    old_coverage = {code: _coverage_bounds(store, code, interval, adjust) for code in normalized_codes}

    started = time.perf_counter()
    failures: list[HydrationFailure] = []
    succeeded = 0
    fetched_rows = 0
    limiter = GlobalRateLimiter(requests_per_second)
    max_workers = min(16, max(1, int(workers)))

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="market-hydrate") as pool:
        futures = {
            pool.submit(
                _fetch_task,
                provider_factory,
                limiter,
                code,
                left,
                right,
                interval,
                adjust,
                retries,
                base_delay,
            ): (code, left, right)
            for code, left, right in tasks
        }
        for future in as_completed(futures):
            code, left, right = futures[future]
            try:
                df = future.result()
                if df is None or df.empty:
                    raise NoMarketData("provider returned empty history")

                old_low, old_high = old_coverage.get(code, (None, None))
                scale_changed = False
                if _is_adjusted(adjust) and old_low is not None and old_high is not None:
                    old_low = pd.Timestamp(old_low).normalize()
                    old_high = pd.Timestamp(old_high).normalize()
                    overlap_left = max(left, old_low)
                    overlap_right = min(right, old_high)
                    if overlap_left <= overlap_right:
                        cached_overlap = _load_history(store, code, interval, overlap_left, overlap_right, adjust)
                        scale_changed = _adjustment_scale_changed(cached_overlap, df)

                if scale_changed:
                    rebuild_left = min(start_ts, old_low) if old_low is not None else start_ts
                    rebuild_right = max(end_ts, old_high) if old_high is not None else end_ts
                    rebuilt = _fetch_task(
                        provider_factory,
                        limiter,
                        code,
                        rebuild_left,
                        rebuild_right,
                        interval,
                        adjust,
                        retries,
                        base_delay,
                    )
                    if rebuilt is None or rebuilt.empty:
                        raise NoMarketData("provider returned empty history during adjusted-cache rebuild")
                    if _clear_history(store, code, interval, adjust):
                        _save_history(store, code, interval, rebuilt, adjust)
                    else:
                        _save_history(store, code, interval, rebuilt, adjust)
                    _mark_coverage(store, code, interval, rebuild_left, rebuild_right, adjust)
                    succeeded += 1
                    fetched_rows += len(rebuilt)
                    continue

                _save_history(store, code, interval, df, adjust)
                if interval in {"1d", "day"}:
                    covered_left, covered_right = left, right
                else:
                    returned = pd.to_datetime(df["datetime"], errors="coerce").dropna()
                    if returned.empty:
                        raise NoMarketData("provider returned no parseable timestamps")
                    covered_left = pd.Timestamp(returned.min()).normalize()
                    covered_right = pd.Timestamp(returned.max()).normalize()
                _mark_coverage(store, code, interval, covered_left, covered_right, adjust)
                succeeded += 1
                fetched_rows += len(df)
            except NoMarketData:
                span_days = int((right - left).days) + 1
                if interval in {"1d", "day"} and span_days <= 4:
                    _mark_coverage(store, code, interval, left, right, adjust)
                    succeeded += 1
                else:
                    failures.append(
                        HydrationFailure(
                            code,
                            left.date().isoformat(),
                            right.date().isoformat(),
                            f"no market data for {span_days}-day range",
                        )
                    )
            except Exception as exc:
                failures.append(
                    HydrationFailure(
                        code=code,
                        start=left.date().isoformat(),
                        end=right.date().isoformat(),
                        error=str(exc),
                    )
                )

    return HydrationReport(
        codes=len(normalized_codes),
        requested_ranges=len(tasks),
        succeeded_ranges=succeeded,
        failed_ranges=len(failures),
        fetched_rows=fetched_rows,
        elapsed_seconds=round(time.perf_counter() - started, 3),
        failures=failures,
    )
