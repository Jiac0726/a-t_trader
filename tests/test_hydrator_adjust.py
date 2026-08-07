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


class AbsoluteScaleProvider(MarketDataProvider):
    name = "absolute-scale"
    scale = 1.0
    calls: list[tuple[pd.Timestamp, pd.Timestamp, float]] = []

    @classmethod
    def reset(cls, scale: float = 1.0):
        cls.scale = float(scale)
        cls.calls = []

    def stock_list(self):
        return pd.DataFrame([{"code": "600519", "name": "TEST", "market": "SH"}])

    def history(self, code, start, end, interval="1d", adjust="qfq"):
        left, right = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        type(self).calls.append((left, right, float(type(self).scale)))
        dates = pd.date_range(left, right, freq="D")
        # Absolute-date pricing is critical here: overlapping dates have the
        # same value when scale is unchanged regardless of request boundaries.
        ordinal = (dates - pd.Timestamp("2026-01-01")).days.astype(float) + 1.0
        base = (100.0 + ordinal) * float(type(self).scale)
        out = pd.DataFrame(
            {
                "datetime": dates,
                "open": base,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + 0.5,
                "volume": [1000.0] * len(dates),
                "amount": [1_000_000.0] * len(dates),
            }
        )
        out.attrs["provider"] = self.name
        return out


def test_adjusted_hydrator_rebuilds_only_after_overlap_scale_change(tmp_path):
    AbsoluteScaleProvider.reset(1.0)
    store = DuckDBStore(tmp_path / "hydrate-scale.duckdb")

    first = hydrate_codes(
        ["600519"],
        AbsoluteScaleProvider,
        store,
        date(2026, 1, 1),
        date(2026, 1, 10),
        adjust="qfq",
        workers=1,
        requests_per_second=0,
        retries=0,
        adjusted_overlap_days=4,
    )
    assert first.failed_ranges == 0
    assert len(AbsoluteScaleProvider.calls) == 1

    # Same scale: Jan11-Jan15 extension deliberately starts with an overlap,
    # but no full rebuild should occur.
    second = hydrate_codes(
        ["600519"],
        AbsoluteScaleProvider,
        store,
        date(2026, 1, 1),
        date(2026, 1, 15),
        adjust="qfq",
        workers=1,
        requests_per_second=0,
        retries=0,
        adjusted_overlap_days=4,
    )
    assert second.failed_ranges == 0
    assert len(AbsoluteScaleProvider.calls) == 2
    assert AbsoluteScaleProvider.calls[-1][0] == pd.Timestamp("2026-01-06")
    assert AbsoluteScaleProvider.calls[-1][1] == pd.Timestamp("2026-01-15")

    # Simulate a corporate action: the provider's entire historical qfq scale
    # changes. The overlap request must detect that and force one full rebuild.
    AbsoluteScaleProvider.scale = 0.5
    third = hydrate_codes(
        ["600519"],
        AbsoluteScaleProvider,
        store,
        date(2026, 1, 1),
        date(2026, 1, 20),
        adjust="qfq",
        workers=1,
        requests_per_second=0,
        retries=0,
        adjusted_overlap_days=4,
    )
    assert third.failed_ranges == 0
    assert len(AbsoluteScaleProvider.calls) == 4
    assert AbsoluteScaleProvider.calls[-2][0] == pd.Timestamp("2026-01-11")
    assert AbsoluteScaleProvider.calls[-2][1] == pd.Timestamp("2026-01-20")
    assert AbsoluteScaleProvider.calls[-1][0] == pd.Timestamp("2026-01-01")
    assert AbsoluteScaleProvider.calls[-1][1] == pd.Timestamp("2026-01-20")

    persisted = store.load_history("600519", "1d", adjust="qfq")
    assert len(persisted) == 20
    expected_first_close = (101.0 * 0.5) + 0.5
    assert float(persisted.iloc[0]["close"]) == expected_first_close
