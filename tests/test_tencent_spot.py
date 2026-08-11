from __future__ import annotations

import pandas as pd
import pytest

from providers.base import MarketDataError
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


def test_tencent_spot_rejects_partial_batch_failure():
    class Response:
        text = _line("sh600519", "贵州茅台", "600519")
        encoding = ""

        @staticmethod
        def raise_for_status():
            return None

    class Session:
        def __init__(self):
            self.headers = {}
            self.calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("simulated failed batch")
            return Response()

    provider = TencentSpotProvider(batch_size=1, session=Session())
    with pytest.raises(MarketDataError, match="partial universe"):
        provider.quotes(["600519", "000001"])
    assert provider.last_report.returned == 1
    assert provider.last_report.failed_batches == 1


def test_tencent_spot_rejects_low_code_coverage_without_batch_error():
    class Response:
        text = _line("sh600519", "贵州茅台", "600519")
        encoding = ""

        @staticmethod
        def raise_for_status():
            return None

    class Session:
        headers = {}

        @staticmethod
        def get(*_args, **_kwargs):
            return Response()

    provider = TencentSpotProvider(batch_size=80, session=Session(), min_coverage_ratio=0.9)
    with pytest.raises(MarketDataError, match="coverage too low"):
        provider.quotes(["600519", "000001"])
