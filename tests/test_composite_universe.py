from __future__ import annotations

import pandas as pd

from providers.composite_universe import BaostockBseUniverseProvider


class SecurityMaster:
    name = "fake-master"

    def snapshot(self, requested):
        rows = []
        for i in range(1500):
            rows.append({"code": f"6{i:05d}", "name": f"SH{i}", "exchange": "SH"})
            rows.append({"code": f"0{i:05d}", "name": f"SZ{i}", "exchange": "SZ"})
        out = pd.DataFrame(rows)
        out.attrs["resolved_trade_date"] = "2026-08-07"
        return out


class BseProvider:
    def bse_stock_list(self):
        return pd.DataFrame(
            [
                {
                    "code": "920000",
                    "name": "migrated",
                    "market": "BJ",
                    "listing_date": pd.Timestamp("2021-11-15"),
                    "source": "bse-official",
                },
                {
                    "code": "920003",
                    "name": "native",
                    "market": "BJ",
                    "listing_date": pd.Timestamp("2025-11-07"),
                    "source": "bse-official",
                },
            ]
        )


def test_composite_universe_prefers_native_920_for_bj_smoke_sampling():
    provider = BaostockBseUniverseProvider(SecurityMaster(), BseProvider(), as_of="2026-08-07")
    stocks = provider.stock_list()
    bj = stocks[stocks["market"] == "BJ"].reset_index(drop=True)

    assert bj.iloc[0]["code"] == "920003"
    assert set(stocks["market"]) == {"SH", "SZ", "BJ"}
    assert stocks.attrs["snapshot_as_of"] == "2026-08-07"
