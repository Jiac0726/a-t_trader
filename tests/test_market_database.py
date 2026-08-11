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


def test_preferred_local_snapshot_fills_only_missing_raw_codes_from_qfq(tmp_path):
    db = MarketDatabase(tmp_path / "mixed-snapshot.duckdb")
    db.sync_universe(pd.DataFrame([
        {"code": "600000", "name": "浦发银行", "market": "SH"},
        {"code": "000001", "name": "平安银行", "market": "SZ"},
    ]), source="fixture")
    db.store.save_history("600000", "1d", _raw_bars(), adjust="none")
    qfq = _raw_bars().copy()
    qfq[["open", "high", "low", "close"]] *= 0.5
    db.store.save_history("600000", "1d", qfq, adjust="qfq")
    db.store.save_history("000001", "1d", qfq, adjust="qfq")

    snap = db.latest_daily_snapshot_prefer_raw().set_index("code")
    assert set(snap.index) == {"600000", "000001"}
    assert snap.loc["600000", "source"] == "local-latest-daily"
    assert snap.loc["000001", "source"] == "local-latest-daily-qfq-fallback"
    assert snap.attrs["qfq_fallback_codes"] == 1
