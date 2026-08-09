from __future__ import annotations

import pandas as pd

from providers.tencent_spot import TencentSpotProvider


def _line(symbol: str, name: str, code: str, price=10.0, prev=9.5, high=10.5, low=9.3, amount_wan=25000.0, turnover=2.0):
    fields = [""] * 78
    fields[0] = "1"
    fields[1] = name
    fields[2] = code
    fields[3] = str(price)
    fields[4] = str(prev)
    fields[5] = str(prev)
    fields[30] = "20260809130000"
    fields[32] = str((price - prev) / prev * 100)
    fields[33] = str(high)
    fields[34] = str(low)
    fields[36] = "10000"
    fields[37] = str(amount_wan)
    fields[38] = str(turnover)
    fields[43] = str((high - low) / prev * 100)
    fields[44] = "120"
    fields[45] = "100"
    return f'v_{symbol}="' + "~".join(fields) + '";'


def test_tencent_spot_routes_920_to_bj():
    assert TencentSpotProvider._prefix("920002") == "bj"
    assert TencentSpotProvider._prefix("600519") == "sh"
    assert TencentSpotProvider._prefix("300750") == "sz"


def test_tencent_spot_parses_screening_metrics():
    row = TencentSpotProvider._parse_line(_line("bj920002", "万达轴承", "920002").rstrip(";"))
    assert row is not None
    assert row["market"] == "BJ"
    assert row["code"] == "920002"
    assert row["amount"] == 25000.0 * 10000.0
    assert row["turnover"] == 2.0
    assert row["amplitude"] == (10.5 - 9.3) / 9.5 * 100
    assert row["total_mcap_yi"] == 120.0
    assert row["float_mcap_yi"] == 100.0
    assert pd.notna(row["pct_change"])


def test_tencent_spot_parses_multiple_assignments_on_one_line():
    text = _line("sh600519", "贵州茅台", "600519") + _line("sz300750", "宁德时代", "300750")
    rows = TencentSpotProvider._parse_response(text)
    assert [row["code"] for row in rows] == ["600519", "300750"]
    assert [row["market"] for row in rows] == ["SH", "SZ"]
