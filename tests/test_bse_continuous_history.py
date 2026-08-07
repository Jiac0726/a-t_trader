from __future__ import annotations

import pandas as pd

from providers.bse_continuous_history import BseContinuousHistoryProvider


class Mapping:
    switch_date = pd.Timestamp("2025-10-09")

    def old_code_for(self, new_code):
        return {"920000": "832000"}.get(str(new_code))


class History:
    name = "fake-history"

    def __init__(self):
        self.calls = []

    def history(self, code, start, end, interval="1d", adjust="qfq"):
        self.calls.append((str(code), pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), interval))
        if str(code) == "832000":
            dates = pd.to_datetime(["2025-10-07", "2025-10-08"])
        elif str(code) == "920000":
            dates = pd.to_datetime(["2025-10-09", "2025-10-10"])
        else:
            dates = pd.to_datetime(["2025-10-10", "2025-10-13"])
        return pd.DataFrame(
            {
                "datetime": dates,
                "open": [10.0] * len(dates),
                "high": [11.0] * len(dates),
                "low": [9.0] * len(dates),
                "close": [10.5] * len(dates),
                "volume": [1000.0] * len(dates),
                "amount": [1_000_000.0] * len(dates),
            }
        )


def test_bse_continuous_history_splits_exactly_at_official_switch_date():
    raw = History()
    provider = BseContinuousHistoryProvider(raw, Mapping())
    out = provider.history("920000", "2025-10-01", "2025-10-20")

    assert [x[0] for x in raw.calls] == ["832000", "920000"]
    assert raw.calls[0][2] == pd.Timestamp("2025-10-08")
    assert raw.calls[1][1] == pd.Timestamp("2025-10-09")
    assert list(out["source_code"]) == ["832000", "832000", "920000", "920000"]
    assert out.attrs["bse_code_stitched"] is True
    assert out.attrs["legacy_code"] == "832000"
    assert out.attrs["current_code"] == "920000"


def test_bse_continuous_history_does_not_guess_unmapped_920_code():
    raw = History()
    provider = BseContinuousHistoryProvider(raw, Mapping())
    out = provider.history("920999", "2025-10-10", "2025-10-20")

    assert [x[0] for x in raw.calls] == ["920999"]
    assert out.attrs["bse_code_stitched"] is False


def test_bse_continuous_history_does_not_claim_minute_stitching():
    raw = History()
    provider = BseContinuousHistoryProvider(raw, Mapping())
    provider.history("920000", "2025-10-01", "2025-10-20", interval="5m")

    assert len(raw.calls) == 1
    assert raw.calls[0][0] == "920000"
    assert raw.calls[0][3] == "5m"
