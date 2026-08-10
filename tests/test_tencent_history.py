from __future__ import annotations

import pandas as pd
import pytest

from providers.base import NoMarketData
from providers.tencent_history import TencentHistoryProvider


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        value = params["param"]
        symbol = value.split(",", 1)[0]
        if "fqkline" in url:
            return Response({"data": {symbol: {"qfqday": [
                ["2026-08-05", "10", "10.5", "11", "9.8", "1000"],
                ["2026-08-06", "10.5", "10.8", "11.2", "10.2", "1200"],
            ]}}})
        return Response({"data": {symbol: {"m5": [
            ["202608060935", "10", "10.1", "10.2", "9.9", "100", "1", "2"],
            ["202608060940", "10.1", "10.2", "10.3", "10.0", "120", "1", "2"],
        ]}}})


def test_tencent_routes_920_to_bj_and_normalizes_daily():
    session = Session()
    provider = TencentHistoryProvider(session=session)
    out = provider.history("920185", "2026-08-01", "2026-08-07")
    assert session.calls[0][1]["param"].startswith("bj920185,day,")
    assert len(out) == 2
    assert out.attrs["tencent_symbol"] == "bj920185"
    assert out.attrs["amount_estimated"] is True
    assert out["amount"].gt(0).all()


def test_tencent_routes_legacy_bse_codes_to_bj_for_history_only():
    assert TencentHistoryProvider._symbol("832000") == "bj832000"
    assert TencentHistoryProvider._symbol("430017") == "bj430017"
    assert TencentHistoryProvider._symbol("873706") == "bj873706"
    assert TencentHistoryProvider._symbol("600519") == "sh600519"
    assert TencentHistoryProvider._symbol("300750") == "sz300750"


def test_tencent_supports_5m_and_keeps_amount_quality_explicit():
    session = Session()
    provider = TencentHistoryProvider(session=session)
    out = provider.history("920185", "2026-08-06", "2026-08-06", interval="5m")
    assert session.calls[0][1]["param"] == "bj920185,m5,,320"
    assert list(pd.to_datetime(out["datetime"]).dt.strftime("%H:%M")) == ["09:35", "09:40"]
    assert out.attrs["amount_quality"].startswith("estimated_")


def test_tencent_daily_raw_fails_closed_instead_of_mislabeling_qfq_as_none():
    session = Session()
    provider = TencentHistoryProvider(session=session)
    with pytest.raises(NoMarketData, match="verified raw/none"):
        provider.history("600519", "2026-08-01", "2026-08-07", interval="1d", adjust="none")
    assert session.calls == []
