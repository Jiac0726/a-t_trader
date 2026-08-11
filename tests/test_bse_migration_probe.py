from __future__ import annotations

import pandas as pd

from app.live_validate_cli import bse_migration_probe


def bars(start: str, n: int = 30):
    dt = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": [10.0] * n,
            "high": [11.0] * n,
            "low": [9.0] * n,
            "close": [10.5] * n,
            "volume": [1000.0] * n,
            "amount": [1_000_000.0] * n,
        }
    )


class Mapping:
    switch_date = pd.Timestamp("2025-10-09")

    def mapping(self):
        return pd.DataFrame([{"old_code": "837023", "new_code": "920123"}])


class History:
    name = "fake-history"

    def history(self, code, start, end, interval="1d", adjust="qfq"):
        return bars("2025-08-01" if str(code) == "837023" else "2025-10-09")


def test_bse_migration_probe_passes_only_when_both_code_segments_validate():
    result = bse_migration_probe(Mapping(), History())
    assert result.status == "PASS"
    assert result.data["old_code"] == "837023"
    assert result.data["new_code"] == "920123"
    assert result.data["legacy_rows"] == 30
    assert result.data["current_rows"] == 30
