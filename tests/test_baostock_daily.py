from __future__ import annotations

import pytest

from providers.baostock_daily import BaostockHistoryProvider
from providers.base import NoMarketData


class _Login:
    error_code = "0"
    error_msg = "success"


class _Rows:
    error_code = "0"
    error_msg = "success"

    def __init__(self, fields, rows):
        self.fields = fields.split(",")
        self.rows = rows
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

    def query_history_k_data_plus(self, source_code, fields, **kwargs):
        self.kwargs = ((source_code, fields), kwargs)
        if kwargs["frequency"] == "d":
            rows = [
                ["2026-08-05", source_code, "1400", "1410", "1390", "1405", "100", "140500", "0.2", "1", "0.3", "0"],
                ["2026-08-06", source_code, "1405", "1420", "1400", "1415", "120", "169800", "0.3", "1", "0.7", "0"],
            ]
        else:
            rows = [
                ["2026-08-06", "20260806093500000", source_code, "1400", "1405", "1398", "1403", "20", "28060", "2"],
                ["2026-08-06", "20260806094000000", source_code, "1403", "1408", "1402", "1407", "22", "30954", "2"],
            ]
        return _Rows(fields, rows)


def test_baostock_history_normalizes_daily_qfq(monkeypatch):
    fake = FakeBS()
    monkeypatch.setattr(BaostockHistoryProvider, "_bs", staticmethod(lambda: fake))
    df = BaostockHistoryProvider().history("600519", "2026-08-01", "2026-08-07", adjust="qfq")
    assert len(df) == 2
    assert df.attrs["provider"] == "baostock-history"
    args, kwargs = fake.kwargs
    assert args[0] == "sh.600519"
    assert kwargs["frequency"] == "d"
    assert kwargs["adjustflag"] == "2"
    assert fake.logged_out


def test_baostock_history_supports_5m(monkeypatch):
    fake = FakeBS()
    monkeypatch.setattr(BaostockHistoryProvider, "_bs", staticmethod(lambda: fake))
    df = BaostockHistoryProvider().history("000001", "2026-08-06", "2026-08-06", interval="5m")
    assert len(df) == 2
    assert str(df.iloc[0]["datetime"]) == "2026-08-06 09:35:00"
    args, kwargs = fake.kwargs
    assert args[0] == "sz.000001"
    assert kwargs["frequency"] == "5"


def test_baostock_history_rejects_bj_without_false_coverage():
    with pytest.raises(NoMarketData):
        BaostockHistoryProvider().history("920000", "2026-08-01", "2026-08-07")
