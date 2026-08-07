from __future__ import annotations

import pandas as pd

from providers.baostock_master import BaostockSecurityMasterProvider


class _Result:
    def __init__(self, data: pd.DataFrame, code: str = "0", msg: str = "success"):
        self._data = data
        self.error_code = code
        self.error_msg = msg

    def get_data(self):
        return self._data.copy()


class _Login:
    error_code = "0"
    error_msg = "success"


class FakeBaoStock:
    def __init__(self):
        self.logged_out = False
        self.all_stock_days: list[str] = []

    def login(self):
        return _Login()

    def logout(self):
        self.logged_out = True

    def query_trade_dates(self, start_date: str, end_date: str):
        return _Result(pd.DataFrame({
            "calendar_date": ["2026-08-06", "2026-08-07", "2026-08-08", "2026-08-09"],
            "is_trading_day": ["1", "1", "0", "0"],
        }))

    def query_all_stock(self, day: str):
        self.all_stock_days.append(day)
        return _Result(pd.DataFrame([
            ["sh.000001", "1", "上证指数"],
            ["sh.600000", "1", "浦发银行"],
            ["sh.510300", "1", "沪深300ETF"],
            ["sh.900901", "1", "云赛B股"],
            ["sz.000001", "0", "平安银行"],
            ["sz.300750", "1", "宁德时代"],
            ["bj.920001", "1", "北证样例"],
            ["bj.899050", "1", "北证50"],
        ], columns=["code", "tradeStatus", "code_name"]))

    def query_stock_basic(self):
        return _Result(pd.DataFrame([
            ["sh.600000", "浦发银行", "1999-11-10", "", "1", "1"],
            ["sz.000001", "平安银行", "1991-04-03", "", "1", "1"],
            ["sh.600001", "邯郸钢铁", "1998-01-22", "2009-12-29", "1", "0"],
            ["sh.000001", "上证指数", "1991-07-15", "", "2", "1"],
        ], columns=["code", "code_name", "ipoDate", "outDate", "type", "status"]))


def test_snapshot_resolves_weekend_and_filters_non_stocks(monkeypatch):
    fake = FakeBaoStock()
    monkeypatch.setattr(BaostockSecurityMasterProvider, "_bs", staticmethod(lambda: fake))
    out = BaostockSecurityMasterProvider().snapshot("2026-08-09")
    assert fake.all_stock_days == ["2026-08-07"]
    assert out.attrs["requested_as_of"] == "2026-08-09"
    assert out.attrs["resolved_trade_date"] == "2026-08-07"
    assert set(out["code"]) == {"600000", "000001", "300750", "920001"}
    assert out.loc[out["code"] == "000001", "trade_status"].iloc[0] == 0
    assert "899050" not in set(out["code"])
    assert fake.logged_out is True


def test_master_normalizes_lifecycle_and_filters_non_stock(monkeypatch):
    fake = FakeBaoStock()
    monkeypatch.setattr(BaostockSecurityMasterProvider, "_bs", staticmethod(lambda: fake))
    out = BaostockSecurityMasterProvider().master()
    assert set(out["code"]) == {"600000", "000001", "600001"}
    old = out[out["code"] == "600001"].iloc[0]
    assert str(old["delist_date"].date()) == "2009-12-29"
    assert old["listed_status"] == 0
    assert "000001" in set(out[out["exchange"] == "SZ"]["code"])
    assert fake.logged_out is True


def test_snapshot_many_reuses_one_session_and_queries_each_unique_trade_date(monkeypatch):
    fake = FakeBaoStock()
    monkeypatch.setattr(BaostockSecurityMasterProvider, "_bs", staticmethod(lambda: fake))
    out = BaostockSecurityMasterProvider().snapshot_many(["2026-08-07", "2026-08-09"])
    assert fake.all_stock_days == ["2026-08-07"]
    assert set(out["code"]) == {"600000", "000001", "300750", "920001"}
    assert fake.logged_out is True
