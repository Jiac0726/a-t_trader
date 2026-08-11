from __future__ import annotations

from pathlib import Path

import pandas as pd


class DuckDBSecuritySnapshotStore:
    """Dedicated DuckDB store for point-in-time security snapshots."""

    def __init__(self, path: str | Path = "market.duckdb"):
        self.path = str(path)

    def _connect(self):
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("duckdb is not installed. Run: pip install duckdb") from exc
        return duckdb.connect(self.path)

    def save_security_snapshot(self, snapshot: pd.DataFrame, provider: str) -> None:
        if snapshot is None or snapshot.empty:
            return
        temp = snapshot.copy()
        temp["as_of"] = pd.to_datetime(temp["as_of"]).dt.normalize()
        temp["code"] = temp["code"].astype(str).str.zfill(6)
        temp["provider"] = str(provider)
        temp["cached_at"] = pd.Timestamp.utcnow().tz_localize(None)
        with self._connect() as con:
            con.register("temp_security_snapshot", temp)
            con.execute("CREATE TABLE IF NOT EXISTS security_snapshots AS SELECT * FROM temp_security_snapshot WHERE 1=0")
            for day in temp["as_of"].drop_duplicates().tolist():
                con.execute("DELETE FROM security_snapshots WHERE provider = ? AND as_of = ?", [str(provider), pd.Timestamp(day)])
            con.execute("INSERT INTO security_snapshots SELECT * FROM temp_security_snapshot")
            con.unregister("temp_security_snapshot")

    def load_security_snapshot(self, as_of, provider: str) -> pd.DataFrame:
        day = pd.Timestamp(as_of).normalize()
        with self._connect() as con:
            try:
                return con.execute(
                    "SELECT * FROM security_snapshots WHERE provider = ? AND as_of = ? ORDER BY exchange, code",
                    [str(provider), day],
                ).df()
            except Exception:
                return pd.DataFrame()
