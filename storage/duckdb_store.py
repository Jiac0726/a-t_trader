from __future__ import annotations

from pathlib import Path
import pandas as pd


class DuckDBStore:
    def __init__(self, path: str | Path = "market.duckdb"):
        self.path = str(path)

    def _connect(self):
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("duckdb is not installed. Run: pip install duckdb") from exc
        return duckdb.connect(self.path)

    def save_history(self, code: str, interval: str, df: pd.DataFrame) -> None:
        table = f"kline_{interval.replace('m','min').replace('1d','day')}"
        temp = df.copy()
        temp["code"] = str(code).zfill(6)
        with self._connect() as con:
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM temp WHERE 1=0"
            )
            con.execute(f"DELETE FROM {table} WHERE code = ?", [str(code).zfill(6)])
            con.execute(f"INSERT INTO {table} SELECT * FROM temp")

    def save_scores(self, scores: pd.DataFrame) -> None:
        if scores.empty:
            return
        temp = scores.copy()
        with self._connect() as con:
            con.execute("CREATE OR REPLACE TABLE t_scores AS SELECT * FROM temp")

    def load_scores(self) -> pd.DataFrame:
        with self._connect() as con:
            try:
                return con.execute("SELECT * FROM t_scores ORDER BY score DESC").df()
            except Exception:
                return pd.DataFrame()
