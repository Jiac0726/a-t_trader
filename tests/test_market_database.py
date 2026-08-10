from __future__ import annotations

import pandas as pd

from storage.market_database import MarketDatabase


def _raw_bars():
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-08-07", "2026-08-08"]),
            "open": [10.0, 10.2],
            "high": [10.5, 10.8],
            "low": [9.8, 10.1],
            "close": [10.2, 10.6],
            "volume": [1000, 1200],
            "amount": [10_000_000, 12_000_000],
        }
    )
    df.attrs["provider"] = "fixture"
    return df


def test_market_database_syncs_universe_and_builds_local_latest_snapshot(tmp_path):
    db = MarketDatabase(tmp_path / "market.duckdb")
    stocks = pd.DataFrame(
        [
            {"code": "600000", "name": "浦发银行", "market": "SH"},
            {"code": "000001", "name": "平安银行", "market": "SZ"},
        ]
    )
    assert db.sync_universe(stocks, source="fixture") == 2

    db.store.save_history("600000", "1d", _raw_bars(), adjust="none")
    snap = db.latest_daily_snapshot("none")
    row = snap[snap["code"] == "600000"].iloc[0]

    assert row["market"] == "SH"
    assert row["name"] == "浦发银行"
    assert row["price"] == 10.6
    assert row["amount"] == 12_000_000
    assert round(float(row["pct_change"]), 6) == round((10.6 / 10.2 - 1) * 100, 6)
    assert round(float(row["amplitude"]), 6) == round((10.8 - 10.1) / 10.2 * 100, 6)

    stats = db.stats()
    assert stats.universe_rows == 2
    assert stats.raw_daily_codes == 1
    assert stats.raw_daily_rows == 2
