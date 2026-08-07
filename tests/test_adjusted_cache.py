from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from data.cached_provider import CachedProvider
from providers.base import MarketDataProvider
from storage.duckdb_store import DuckDBStore


def frame(start: str, end: str, scale: float = 1.0):
    dates = pd.date_range(start, end, freq="D")
    base = pd.Series(range(1, len(dates) + 1), dtype=float) * scale
    return pd.DataFrame(
        {
            "datetime": dates,
            "open": base + 10,
            "high": base + 11,
            "low": base + 9,
            "close": base + 10.5,
            "volume": [1000.0] * len(dates),
            "amount": [1_000_000.0] * len(dates),
        }
    )


def test_duckdb_history_and_coverage_are_namespaced_by_adjust(tmp_path):
    store = DuckDBStore(tmp_path / "adjust.duckdb")
    qfq = frame("2026-01-01", "2026-01-03", scale=1.0)
    raw = frame("2026-01-01", "2026-01-03", scale=2.0)

    store.save_history("600519", "1d", qfq, adjust="qfq")
    store.save_history("600519", "1d", raw, adjust="none")
    store.mark_history_coverage("600519", "1d", "2026-01-01", "2026-01-03", adjust="qfq")
    store.mark_history_coverage("600519", "1d", "2026-01-01", "2026-01-05", adjust="none")

    qfq_out = store.load_history("600519", "1d", adjust="qfq")
    raw_out = store.load_history("600519", "1d", adjust="none")

    assert len(qfq_out) == len(raw_out) == 3
    assert qfq_out.iloc[0]["close"] != raw_out.iloc[0]["close"]
    assert set(qfq_out["adjust"]) == {"qfq"}
    assert set(raw_out["adjust"]) == {"none"}
    assert store.coverage_bounds("600519", "1d", adjust="qfq")[1] == pd.Timestamp("2026-01-03")
    assert store.coverage_bounds("600519", "1d", adjust="none")[1] == pd.Timestamp("2026-01-05")


class ScaleChangingProvider(MarketDataProvider):
    name = "scale-changing"

    def __init__(self):
        self.scale = 1.0
        self.calls = []

    def history(self, code, start, end, interval="1d", adjust="qfq"):
        self.calls.append((str(start), str(end), adjust, self.scale))
        out = frame(str(start), str(end), self.scale)
        out.attrs["provider"] = self.name
        out.attrs["name"] = "TEST"
        return out

    def stock_list(self):
        return pd.DataFrame([{"code": "600519", "name": "TEST", "market": "SH"}])


def test_qfq_overlap_scale_change_rebuilds_entire_cached_span(tmp_path):
    raw = ScaleChangingProvider()
    store = DuckDBStore(tmp_path / "scale.duckdb")
    cached = CachedProvider(raw, store, adjusted_overlap_days=5)

    first = cached.history("600519", date(2026, 1, 1), date(2026, 1, 10), adjust="qfq")
    first_close = float(first.iloc[0]["close"])
    assert len(raw.calls) == 1

    # Simulate a corporate action: upstream qfq history is now on a new scale.
    raw.scale = 0.5
    second = cached.history("600519", date(2026, 1, 1), date(2026, 1, 15), adjust="qfq")

    # One overlap/tail request detects the changed scale, then one full rebuild.
    assert len(raw.calls) == 3
    assert float(second.iloc[0]["close"]) != pytest.approx(first_close)
    assert float(second.iloc[0]["close"]) == pytest.approx(float(frame("2026-01-01", "2026-01-15", 0.5).iloc[0]["close"]))
    persisted = store.load_history("600519", "1d", adjust="qfq")
    assert len(persisted) == 15
    assert float(persisted.iloc[0]["close"]) == pytest.approx(float(second.iloc[0]["close"]))


def test_cached_provider_fetches_raw_and_qfq_as_separate_namespaces(tmp_path):
    raw = ScaleChangingProvider()
    store = DuckDBStore(tmp_path / "modes.duckdb")
    cached = CachedProvider(raw, store)

    cached.history("600519", date(2026, 1, 1), date(2026, 1, 5), adjust="qfq")
    cached.history("600519", date(2026, 1, 1), date(2026, 1, 5), adjust="none")

    assert len(raw.calls) == 2
    assert {call[2] for call in raw.calls} == {"qfq", "none"}
    assert len(store.load_history("600519", "1d", adjust="qfq")) == 5
    assert len(store.load_history("600519", "1d", adjust="none")) == 5
