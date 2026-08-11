from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.live_validate_cli import hosted_universe_probe, tencent_spot_probe


class ShSzUniverse:
    name = "fake-hosted-universe"

    def stock_list(self):
        rows = []
        for i in range(1600):
            rows.append({"code": f"6{i:05d}", "name": f"SH{i}", "market": "SH"})
            rows.append({"code": f"0{i:05d}", "name": f"SZ{i}", "market": "SZ"})
        return pd.DataFrame(rows)


class FullUniverse(ShSzUniverse):
    def stock_list(self):
        out = super().stock_list()
        bj = pd.DataFrame([{"code": "920002", "name": "BJ", "market": "BJ"}])
        return pd.concat([out, bj], ignore_index=True)


def test_hosted_universe_probe_warns_only_when_bj_is_missing():
    partial = hosted_universe_probe(ShSzUniverse())
    assert partial.status == "WARN"
    assert partial.data["markets"] == {"SH": 1600, "SZ": 1600}

    full = hosted_universe_probe(FullUniverse())
    assert full.status == "PASS"
    assert full.data["markets"]["BJ"] == 1


@dataclass
class Report:
    requested: int = 3
    returned: int = 3
    failed_batches: int = 0


class GoodSpot:
    name = "fake-spot"
    last_report = Report()

    def quotes(self, codes):
        rows = []
        for code, market in [("600519", "SH"), ("000001", "SZ"), ("300750", "SZ")]:
            rows.append({
                "code": code,
                "market": market,
                "price": 10.0,
                "amount": 3e8,
                "turnover": 1.2,
                "amplitude": 3.5,
                "pct_change": 0.5,
                "quote_time": "20260807150000",
            })
        return pd.DataFrame(rows)


class BadSpot(GoodSpot):
    def quotes(self, codes):
        out = super().quotes(codes)
        out.loc[out["code"] == "300750", "amount"] = float("nan")
        return out


def test_tencent_spot_probe_requires_complete_screening_fields():
    ok = tencent_spot_probe(GoodSpot())
    assert ok.status == "PASS"
    assert ok.data["rows"] == 3
    assert ok.data["quote_time"] == "20260807150000"

    bad = tencent_spot_probe(BadSpot())
    assert bad.status == "FAIL"
    assert "amount" in bad.detail
