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


def plan_missing_ranges(
    codes: list[str],
    store: DuckDBStore,
    start: date | str | pd.Timestamp,
    end: date | str | pd.Timestamp,
    interval: str = "1d",
) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    start_ts = _normalize_day(start)
    end_ts = _normalize_day(end)
    tasks: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
    for raw_code in codes:
        code = str(raw_code).zfill(6)
        coverage_fn = getattr(store, "coverage_bounds", None)
        if callable(coverage_fn):
            low, high = coverage_fn(code, interval)
        else:
            low, high = store.history_bounds(code, interval)
        if low is None or high is None:
            tasks.append((code, start_ts, end_ts))
            continue
        low = low.normalize()
        high = high.normalize()
        if start_ts < low:
            tasks.append((code, start_ts, low - pd.Timedelta(days=1)))
        if end_ts > high:
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
) -> HydrationReport:
    """Fetch missing K-line ranges concurrently, write them to DuckDB serially.

    Fetch workers each create their own provider instance so requests.Session and
    provider-local state are not shared across threads. DuckDB writes stay on the
    caller thread, avoiding concurrent writer conflicts.
    """

    normalized_codes = list(dict.fromkeys(str(c).zfill(6) for c in codes if str(c).strip()))
    tasks = plan_missing_ranges(normalized_codes, store, start, end, interval=interval)
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
                store.save_history(code, interval, df)
                mark_fn = getattr(store, "mark_history_coverage", None)
                if callable(mark_fn):
                    mark_fn(code, interval, left, right)
                succeeded += 1
                fetched_rows += len(df)
            except NoMarketData:
                mark_fn = getattr(store, "mark_history_coverage", None)
                if callable(mark_fn):
                    mark_fn(code, interval, left, right)
                    succeeded += 1
                else:
                    failures.append(HydrationFailure(code, left.date().isoformat(), right.date().isoformat(), "no market data"))
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
