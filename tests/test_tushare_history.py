from __future__ import annotations

import pandas as pd
import pytest

from providers.base import MarketDataError
from providers.tushare_history import TushareHistoryProvider


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

    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        api = json["api_name"]
        if api == "daily":
            return Response({
                "code": 0,
                "data": {
                    "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"],
                    "items": [
                        ["920002.BJ", "20260806", 20.0, 22.0, 19.0, 21.0, 20.0, 1.0, 5.0, 100.0, 200.0],
                        ["920002.BJ", "20260805", 10.0, 11.0, 9.0, 10.5, 10.0, 0.5, 5.0, 80.0, 100.0],
                    ],
                },
            })
        if api == "adj_factor":
            return Response({
                "code": 0,
                "data": {
                    "fields": ["ts_code", "trade_date", "adj_factor"],
                    "items": [
                        ["920002.BJ", "20260806", 2.0],
                        ["920002.BJ", "20260805", 1.0],
                    ],
                },
            })
        if api == "stock_basic":
            exchange = json["params"]["exchange"]
            symbol = {"SSE": "600519", "SZSE": "300750", "BSE": "920002"}[exchange]
            return Response({
                "code": 0,
                "data": {
                    "fields": ["ts_code", "symbol", "name", "exchange", "list_date", "delist_date", "list_status"],
                    "items": [[f"{symbol}.X", symbol, "x", exchange, "20240101", None, "L"]],
                },
            })
        raise AssertionError(api)


def test_tushare_requires_token_explicitly():
    provider = TushareHistoryProvider(token="", session=Session())
    with pytest.raises(MarketDataError, match="TUSHARE_TOKEN"):
        provider.history("920002", "2026-08-01", "2026-08-07")


def test_tushare_routes_bse_and_normalizes_units_and_qfq():
    session = Session()
    provider = TushareHistoryProvider(token="secret", session=session)
    out = provider.history("920002", "2026-08-01", "2026-08-07", adjust="qfq")

    assert session.calls[0]["params"]["ts_code"] == "920002.BJ"
    assert list(out["datetime"].dt.strftime("%Y-%m-%d")) == ["2026-08-05", "2026-08-06"]
    # Latest factor=2 anchors the latest row; older factor=1 halves old prices.
    assert out.iloc[0]["close"] == pytest.approx(5.25)
    assert out.iloc[1]["close"] == pytest.approx(21.0)
    assert out.iloc[0]["volume"] == pytest.approx(8000.0)
    assert out.iloc[0]["amount"] == pytest.approx(100000.0)
    assert out.attrs["provider"] == "tushare-history"
    assert out.attrs["tushare_ts_code"] == "920002.BJ"


def test_tushare_stock_list_can_include_bse_identity():
    provider = TushareHistoryProvider(token="secret", session=Session())
    stocks = provider.stock_list()
    assert set(stocks["market"]) == {"SH", "SZ", "BJ"}
    assert "920002" in set(stocks["code"])
