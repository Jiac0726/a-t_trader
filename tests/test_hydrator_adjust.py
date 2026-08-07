from __future__ import annotations

from datetime import date

import pandas as pd

from data.hydrator import hydrate_codes, plan_missing_ranges
from providers.base import MarketDataProvider, NoMarketData
from providers.demo import DemoProvider
from storage.duckdb_store import DuckDBStore


def test_hydrator_keeps_qfq_and_raw_coverage_separate(tmp_path):
    store = DuckDBStore(tmp_path / "hydrate-adjust.duckdb")
    hydrate_codes(
        ["300059"],
        DemoProvider,
        store,
        date(2026, 8, 1),
        date(2026, 8, 7),
        adjust="qfq",
        workers=1,
        requests_per_second=0,
        retries=0,
    )

    raw_missing = plan_missing_ranges(
        ["300059"],
        store,
        date(2026, 8, 1),
        date(2026, 8, 7),
        interval="1d",
        adjust="none",
    )
    qfq_missing = plan_missing_ranges(
        ["300059"],
        store,
        date(2026, 8, 1),
        date(2026, 8, 7),
        interval="1d",
        adjust="qfq",
    )

    assert len(raw_missing) == 1
    assert qfq_missing == []


class AlwaysNoData(MarketDataProvider):
    name = "always-no-data"

    def history(self, code, start, end, interval="1d", adjust="qfq"):
        raise NoMarketData("unsupported")

    def stock_list(self):
        return pd.DataFrame()


def test_long_daily_no_data_range_remains_visible_failure(tmp_path):
    store = DuckDBStore(tmp_path / "long-empty.duckdb")
    report = hydrate_codes(
        ["920002"],
        AlwaysNoData,
        store,
        date(2026, 7, 1),
        date(2026, 7, 10),
        workers=1,
        requests_per_second=0,
        retries=0,
    )

    assert report.succeeded_ranges == 0
    assert report.failed_ranges == 1
    assert "10-day range" in report.failures[0].error
    assert store.coverage_bounds("920002", "1d", adjust="qfq") == (None, None)


def test_short_daily_no_data_gap_can_close_coverage(tmp_path):
    store = DuckDBStore(tmp_path / "short-empty.duckdb")
    report = hydrate_codes(
        ["300059"],
        AlwaysNoData,
        store,
        date(2026, 8, 8),
        date(2026, 8, 9),
        workers=1,
        requests_per_second=0,
        retries=0,
    )

    assert report.succeeded_ranges == 1
    assert report.failed_ranges == 0
    low, high = store.coverage_bounds("300059", "1d", adjust="qfq")
    assert low == pd.Timestamp("2026-08-08")
    assert high == pd.Timestamp("2026-08-09")
