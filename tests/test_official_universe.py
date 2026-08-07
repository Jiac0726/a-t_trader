from __future__ import annotations

import json

import pandas as pd
import pytest

from providers.official_universe import OfficialExchangeUniverseProvider
from scanner.market_scanner import select_universe


class Response:
    def __init__(self, *, json_data=None, text=""):
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


class FakeSession:
    def __init__(self):
        self.headers = {"User-Agent": "test"}
        self.sz_pages = []
        self.bj_pages = []

    def get(self, url, params=None, headers=None, timeout=None):
        if "sseQuery" in url:
            stock_type = params["STOCK_TYPE"]
            prefix = "600" if stock_type == "1" else "688"
            return Response(json_data={"result": [
                {"A_STOCK_CODE": f"{prefix}{i:03d}", "SEC_NAME_CN": f"SH{i}", "LIST_DATE": "2020-01-01"}
                for i in range(1000)
            ]})
        if "ShowReport/data" in url:
            page = int(params["PAGENO"])
            self.sz_pages.append(page)
            rows = [
                {"agdm": f"00{i:04d}", "agjc": f"<u>SZ{i}</u>", "agssrq": "2020-01-01"}
                for i in range((page - 1) * 600, min(page * 600, 1200))
            ]
            return Response(json_data=[{"metadata": {"pagecount": 2, "recordcount": 1200, "pagesize": 600}, "data": rows}])
        raise AssertionError(url)

    def post(self, url, data=None, headers=None, timeout=None):
        page = int(data["page"])
        self.bj_pages.append(page)
        rows = []
        start = page * 60
        for i in range(start, start + 60):
            row = [""] * 48
            row[0] = "20240101"
            row[38] = f"920{i:03d}"
            row[40] = f"BJ{i}"
            rows.append(row)
        body = [{"totalPages": 2, "content": rows}]
        return Response(text=json.dumps(body, ensure_ascii=False) + ";")


def test_official_exchange_universe_combines_three_markets():
    provider = OfficialExchangeUniverseProvider(session=FakeSession())
    out = provider.stock_list()
    assert set(out["market"]) == {"SH", "SZ", "BJ"}
    assert len(out[out["market"] == "SH"]) >= 1000
    assert len(out[out["market"] == "SZ"]) == 1200
    assert len(out[out["market"] == "BJ"]) == 120
    assert provider.session.sz_pages == [1, 2]
    assert provider.session.bj_pages == [0, 1]
    assert out.loc[out["market"] == "SZ", "name"].iloc[0].startswith("SZ")


def test_identity_universe_does_not_silently_satisfy_spot_amount_filter():
    class IdentityOnly:
        def stock_list(self):
            return pd.DataFrame({"code": ["600000"], "name": ["X"], "market": ["SH"]})

    with pytest.raises(ValueError, match="amount"):
        select_universe(IdentityOnly(), min_spot_amount=1_000_000)
