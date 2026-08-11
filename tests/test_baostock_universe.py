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


class RejectedMaster:
    def snapshot(self, requested):
        raise RuntimeError("黑名单用户，请与管理员联系")


class FallbackUniverse:
    name = "fake-tencent-discovery"

    def stock_list(self):
        return pd.DataFrame([
            {"code": "600519", "name": "贵州茅台", "market": "SH"},
            {"code": "000001", "name": "平安银行", "market": "SZ"},
            {"code": "920002", "name": "万达轴承", "market": "BJ"},
        ])


def test_baostock_snapshot_universe_allows_explicit_shsz_partial_coverage():
    provider = BaostockSnapshotUniverseProvider(Master(), as_of="2026-08-09", include_bse=False)
    out = provider.stock_list()
    assert len(out) == 3200
    assert set(out["market"]) == {"SH", "SZ"}
    assert out.attrs["market_coverage"] == ["SH", "SZ"]
    assert out.attrs["missing_markets"] == ["BJ"]


def test_baostock_rejection_falls_back_to_tencent_discovery_without_outer_chain_failure():
    provider = BaostockSnapshotUniverseProvider(
        RejectedMaster(),
        as_of="2026-08-09",
        include_bse=False,
        fallback_provider=FallbackUniverse(),
    )
    out = provider.stock_list()
    assert len(out) == 3
    assert set(out["market"]) == {"SH", "SZ", "BJ"}
    assert out.attrs["provider"] == "fake-tencent-discovery"
    assert out.attrs["fallback_from"] == "baostock-snapshot-universe"
    assert "黑名单用户" in out.attrs["fallback_reason"]
    assert out.attrs["missing_markets"] == []
