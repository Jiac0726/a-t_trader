from __future__ import annotations

from datetime import date
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

    @staticmethod
    def _table(interval: str) -> str:
        safe = interval.replace("m", "min").replace("1d", "day").replace("-", "_")
        return f"kline_{safe}"

    def save_stock_list(self, stocks: pd.DataFrame, provider: str = "") -> None:
        if stocks is None or stocks.empty:
            return
        temp = stocks.copy()
        temp["code"] = temp["code"].astype(str).str.zfill(6)
        temp["provider"] = provider
        temp["updated_at"] = pd.Timestamp.utcnow().tz_localize(None)
        with self._connect() as con:
            con.register("temp_stocks", temp)
            con.execute("CREATE OR REPLACE TABLE stock_universe AS SELECT * FROM temp_stocks")
            con.unregister("temp_stocks")

    def load_stock_list(self) -> pd.DataFrame:
        with self._connect() as con:
            try:
                return con.execute("SELECT * FROM stock_universe ORDER BY code").df()
            except Exception:
                return pd.DataFrame()

    def stock_list_age_hours(self) -> float | None:
        with self._connect() as con:
            try:
                row = con.execute("SELECT max(updated_at) FROM stock_universe").fetchone()
            except Exception:
                return None
        if not row or row[0] is None:
            return None
        updated = pd.Timestamp(row[0])
        now = pd.Timestamp.utcnow().tz_localize(None)
        return max(0.0, float((now - updated).total_seconds() / 3600.0))

    def save_history(self, code: str, interval: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        table = self._table(interval)
        temp = df.copy()
        temp["code"] = str(code).zfill(6)
        temp["datetime"] = pd.to_datetime(temp["datetime"])
        with self._connect() as con:
            con.register("temp_history", temp)
            con.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM temp_history WHERE 1=0")
            start = temp["datetime"].min()
            end = temp["datetime"].max()
            con.execute(
                f"DELETE FROM {table} WHERE code = ? AND datetime BETWEEN ? AND ?",
                [str(code).zfill(6), start, end],
            )
            con.execute(f"INSERT INTO {table} SELECT * FROM temp_history")
            con.unregister("temp_history")

    def load_history(
        self,
        code: str,
        interval: str,
        start: date | str | pd.Timestamp | None = None,
        end: date | str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        table = self._table(interval)
        code = str(code).zfill(6)
        clauses = ["code = ?"]
        params: list[object] = [code]
        if start is not None:
            clauses.append("datetime >= ?")
            params.append(pd.Timestamp(start))
        if end is not None:
            clauses.append("datetime <= ?")
            end_ts = pd.Timestamp(end)
            if end_ts.hour == 0 and end_ts.minute == 0 and end_ts.second == 0:
                end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            params.append(end_ts)
        where = " AND ".join(clauses)
        with self._connect() as con:
            try:
                return con.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY datetime", params).df()
            except Exception:
                return pd.DataFrame()

    def history_bounds(self, code: str, interval: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        table = self._table(interval)
        with self._connect() as con:
            try:
                row = con.execute(
                    f"SELECT min(datetime), max(datetime) FROM {table} WHERE code = ?",
                    [str(code).zfill(6)],
                ).fetchone()
            except Exception:
                return None, None
        if not row or row[0] is None:
            return None, None
        return pd.Timestamp(row[0]), pd.Timestamp(row[1])

    def save_scores(self, scores: pd.DataFrame) -> None:
        if scores is None or scores.empty:
            return
        temp = scores.copy()
        temp["scored_at"] = pd.Timestamp.utcnow().tz_localize(None)
        with self._connect() as con:
            con.register("temp_scores", temp)
            con.execute("CREATE OR REPLACE TABLE t_scores AS SELECT * FROM temp_scores")
            con.unregister("temp_scores")

    def load_scores(self) -> pd.DataFrame:
        with self._connect() as con:
            try:
                return con.execute("SELECT * FROM t_scores ORDER BY score DESC").df()
            except Exception:
                return pd.DataFrame()
