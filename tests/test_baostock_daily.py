from __future__ import annotations

import pandas as pd
import pytest

from providers.baostock_daily import BaostockDailyProvider
from providers.base import NoMarketData


class _Login:
    error_code = "0"
    error_msg = "success"


class _Rows:
    error_code = "0"
    error_msg = "success"
    fields = ["date", "code", "open", "high", "low", "close", "volume", "amount", "turn", "tradestatus", "pctChg", "isST"]

    def __init__(self):
        self.rows = [
            ["2026-08-05", "sh.600519", "1400", "1410", "1390", "1405", "100", "140500", "0.2", "1", "0.3", "0"],
            ["2026-08-06", "sh.600519", "1405", "1420", "1400", "1415", "120", "169800", "0.3", "1", "0.7", "0"],
        ]
        self.i = -1

    def next(self):
        self.i += 1
        return self.i < len(self.rows)

    def get_row_data(self):
        return self.rows[self.i]


class FakeBS:
    def __init__(self):
        self.kwargs = None
        self.logged_out = False

    def login(self):
        return _Login()

    def logout(self):
        self.logged_out = True

    def query_history_k_data_plus(self, *args, **kwargs):
        self.kwargs = (args, kwargs)
        return _Rows()


def test_baostock_daily_normalizes_qfq(monkeypatch):
    fake = FakeBS()
    monkeypatch.setattr(BaostockDailyProvider, "_bs", staticmethod(lambda: fake))
    df = BaostockDailyProvider().history("600519", "2026-08-01", "2026-08-07", adjust="qfq")
    assert len(df) == 2
    assert list(df.columns[:6]) == ["datetime", "code", "open", "high", "low", "close"]
    assert df.attrs["provider"] == "baostock-daily"
    args, kwargs = fake.kwargs
    assert args[0] == "sh.600519"
    assert kwargs["adjustflag"] == "2"
    assert fake.logged_out


def test_baostock_daily_rejects_minute_and_bj_without_false_coverage():
    provider = BaostockDailyProvider()
    with pytest.raises(NoMarketData):
        provider.history("600519", "2026-08-01", "2026-08-07", interval="5m")
    with pytest.raises(NoMarketData):
        provider.history("920000", "2026-08-01", "2026-08-07")
