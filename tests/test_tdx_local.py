from __future__ import annotations

import struct

import pandas as pd
import pytest

from providers.base import NoMarketData
from providers.tdx_local import TdxLocalHistoryProvider


def _packed_date(year: int, month: int, day: int) -> int:
    return (year - 2004) * 2048 + month * 100 + day


def test_tdx_local_reads_bj_daily_32_byte_records(tmp_path):
    path = tmp_path / "vipdoc" / "bj" / "lday" / "bj920002.day"
    path.parent.mkdir(parents=True)
    record = struct.Struct("<IIIIIfII")
    path.write_bytes(
        record.pack(20260805, 1000, 1100, 900, 1050, 123456.0, 7890, 0)
        + record.pack(20260806, 1050, 1120, 1020, 1080, 223456.0, 8890, 0)
    )

    provider = TdxLocalHistoryProvider(tmp_path)
    out = provider.history("920002", "2026-08-01", "2026-08-07", adjust="none")

    assert list(out["datetime"].dt.strftime("%Y-%m-%d")) == ["2026-08-05", "2026-08-06"]
    assert out.iloc[0]["open"] == pytest.approx(10.0)
    assert out.iloc[0]["close"] == pytest.approx(10.5)
    assert out.attrs["market"] == "BJ"
    assert out.attrs["source_path"].endswith("vipdoc/bj/lday/bj920002.day")


def test_tdx_local_reads_bj_lc5_minute_record(tmp_path):
    path = tmp_path / "vipdoc" / "bj" / "fzline" / "bj920002.lc5"
    path.parent.mkdir(parents=True)
    record = struct.Struct("<HHfffffII")
    date_num = _packed_date(2026, 8, 6)
    path.write_bytes(record.pack(date_num, 9 * 60 + 35, 10.0, 10.2, 9.9, 10.1, 50000.0, 1000, 0))

    provider = TdxLocalHistoryProvider(tmp_path)
    out = provider.history("920002", "2026-08-06", "2026-08-06", interval="5m", adjust="none")

    assert len(out) == 1
    assert pd.Timestamp(out.iloc[0]["datetime"]).strftime("%Y-%m-%d %H:%M") == "2026-08-06 09:35"
    assert out.iloc[0]["close"] == pytest.approx(10.1)


def test_tdx_local_refuses_to_label_raw_files_as_qfq(tmp_path):
    provider = TdxLocalHistoryProvider(tmp_path)
    with pytest.raises(NoMarketData, match="raw"):
        provider.history("920002", "2026-08-01", "2026-08-07", adjust="qfq")
