from __future__ import annotations

import pandas as pd

from providers.baostock_universe import BaostockSnapshotUniverseProvider


class Master:
    def snapshot(self, requested):
        rows = []
        for i in range(1600):
            rows.append({"code": f"6{i:05d}", "name": f"SH{i}", "exchange": "SH", "trade_status": 1})
            rows.append({"code": f"0{i:05d}", "name": f"SZ{i}", "exchange": "SZ", "trade_status": 1})
        out = pd.DataFrame(rows)
        out.attrs["resolved_trade_date"] = "2026-08-08"
        return out


def test_baostock_snapshot_universe_allows_explicit_shsz_partial_coverage():
    provider = BaostockSnapshotUniverseProvider(Master(), as_of="2026-08-09", include_bse=False)
    out = provider.stock_list()
    assert len(out) == 3200
    assert set(out["market"]) == {"SH", "SZ"}
    assert out.attrs["market_coverage"] == ["SH", "SZ"]
    assert out.attrs["missing_markets"] == ["BJ"]
