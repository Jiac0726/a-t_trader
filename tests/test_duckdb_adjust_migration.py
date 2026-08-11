from __future__ import annotations

import pandas as pd

from storage.duckdb_store import DuckDBStore


def test_old_kline_and_coverage_tables_migrate_to_qfq_namespace(tmp_path):
    import duckdb

    path = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        """
        CREATE TABLE kline_day (
            datetime TIMESTAMP,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE,
            code VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO kline_day VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [pd.Timestamp("2026-01-02"), 10.0, 11.0, 9.0, 10.5, 1000.0, 1_000_000.0, "600519"],
    )
    con.execute(
        """
        CREATE TABLE history_coverage (
            code VARCHAR,
            interval VARCHAR,
            covered_start TIMESTAMP,
            covered_end TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    con.execute(
        "INSERT INTO history_coverage VALUES (?, ?, ?, ?, ?)",
        ["600519", "1d", pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-03"), pd.Timestamp("2026-01-03")],
    )
    con.close()

    store = DuckDBStore(path)
    qfq = store.load_history("600519", "1d", adjust="qfq")
    raw = store.load_history("600519", "1d", adjust="none")
    low, high = store.coverage_bounds("600519", "1d", adjust="qfq")

    assert len(qfq) == 1
    assert set(qfq["adjust"]) == {"qfq"}
    assert raw.empty
    assert low == pd.Timestamp("2026-01-01")
    assert high == pd.Timestamp("2026-01-03")
