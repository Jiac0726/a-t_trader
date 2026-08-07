from __future__ import annotations

import pandas as pd

from data.cached_security_master import CachedSecurityMasterProvider
from providers.security_master import SecurityMasterProvider


class FakeMaster(SecurityMasterProvider):
    def __init__(self):
        self.calls: list[list[str]] = []

    @property
    def name(self):
        return "fake-master"

    def snapshot(self, as_of):
        return self.snapshot_many([as_of])

    def snapshot_many(self, as_of_dates):
        dates = [pd.Timestamp(x).normalize() for x in as_of_dates]
        self.calls.append([str(x.date()) for x in dates])
        rows = []
        for day in dates:
            rows.append({"as_of": day, "code": "600000", "exchange": "SH", "name": "x", "trade_status": 1, "source_code": "sh.600000", "source": self.name})
        return pd.DataFrame(rows)

    def master(self):
        return pd.DataFrame()


class MemorySnapshotStore:
    def __init__(self):
        self.frames: dict[tuple[str, str], pd.DataFrame] = {}

    def load_security_snapshot(self, as_of, provider):
        return self.frames.get((str(pd.Timestamp(as_of).date()), provider), pd.DataFrame()).copy()

    def save_security_snapshot(self, frame, provider):
        if frame is None or frame.empty:
            return
        for day, group in frame.groupby(pd.to_datetime(frame["as_of"]).dt.normalize()):
            self.frames[(str(pd.Timestamp(day).date()), provider)] = group.copy()


def test_snapshot_many_only_fetches_missing_dates():
    raw = FakeMaster()
    store = MemorySnapshotStore()
    cached = CachedSecurityMasterProvider(raw, store)
    first = cached.snapshot_many(["2026-01-05", "2026-01-06"])
    assert len(first) == 2
    assert raw.calls == [["2026-01-05", "2026-01-06"]]
    second = cached.snapshot_many(["2026-01-05", "2026-01-06"])
    assert len(second) == 2
    assert len(raw.calls) == 1
    third = cached.snapshot_many(["2026-01-05", "2026-01-07"])
    assert len(third) == 2
    assert raw.calls[-1] == ["2026-01-07"]
