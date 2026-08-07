from __future__ import annotations

import pandas as pd

from dataset.builder import (
    attach_minute_labels_from_store,
    build_daily_score_panel,
    minute_coverage_report,
    universe_codes_from_lifecycle,
)
from providers.demo import DemoProvider


class MemoryStore:
    def __init__(self, data):
        self.data = data

    def load_history(self, code, interval, start=None, end=None):
        frame = self.data.get((str(code).zfill(6), interval), pd.DataFrame()).copy()
        if frame.empty:
            return frame
        frame["datetime"] = pd.to_datetime(frame["datetime"])
        if start is not None:
            frame = frame[frame["datetime"] >= pd.Timestamp(start)]
        if end is not None:
            end_ts = pd.Timestamp(end)
            if interval != "1d" and end_ts == end_ts.normalize():
                end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            frame = frame[frame["datetime"] <= end_ts]
        return frame.reset_index(drop=True)

    def history_bounds(self, code, interval):
        frame = self.load_history(code, interval)
        if frame.empty:
            return None, None
        return pd.Timestamp(frame["datetime"].min()), pd.Timestamp(frame["datetime"].max())


def _master():
    return pd.DataFrame([
        {"code": "600519", "exchange": "SH", "name": "A", "ipo_date": "2001-01-01", "delist_date": None, "security_type": "stock"},
        {"code": "000001", "exchange": "SZ", "name": "B", "ipo_date": "1991-01-01", "delist_date": None, "security_type": "stock"},
        {"code": "600001", "exchange": "SH", "name": "OLD", "ipo_date": "2026-02-01", "delist_date": "2026-04-01", "security_type": "stock"},
    ])


def test_universe_plan_keeps_security_that_only_existed_inside_range():
    codes = universe_codes_from_lifecycle(_master(), "2026-01-01", "2026-06-01")
    assert {"600519", "000001", "600001"}.issubset(set(codes))


def test_daily_panel_and_real_minute_coverage_are_separate():
    demo = DemoProvider()
    daily_a = demo.history("600519", "2026-01-01", "2026-06-30", "1d")
    daily_b = demo.history("000001", "2026-01-01", "2026-06-30", "1d")
    minute_a = demo.history("600519", "2026-05-01", "2026-06-30", "5m")
    store = MemoryStore({
        ("600519", "1d"): daily_a,
        ("000001", "1d"): daily_b,
        ("600519", "5m"): minute_a,
    })

    panel, failures = build_daily_score_panel(
        ["600519", "000001"], store, start="2026-01-01", end="2026-06-30", lookback=60, master=_master()
    )
    assert panel["code"].nunique() == 2
    assert not failures

    labeled, label_failures = attach_minute_labels_from_store(panel, store, horizon=3)
    assert labeled["code"].nunique() == 2
    a = labeled[labeled["code"] == "600519"]
    b = labeled[labeled["code"] == "000001"]
    assert a["forward_opportunity_pct"].notna().any()
    assert b["forward_opportunity_pct"].isna().all()
    assert any(x.code == "000001" and x.stage == "minute_label" for x in label_failures)

    coverage = minute_coverage_report(["600519", "000001"], store).set_index("code")
    assert coverage.loc["600519", "rows_5m"] > 0
    assert coverage.loc["600519", "trading_days_5m"] > 0
    assert coverage.loc["000001", "rows_5m"] == 0
