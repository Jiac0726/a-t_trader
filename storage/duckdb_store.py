from __future__ import annotations

from datetime import date
from pathlib import Path
import pandas as pd


class DuckDBStore:
    """DuckDB persistence for normalized research data.

    K-line cache identity is ``code + interval + adjust + datetime``. Older MVP
    databases did not contain ``adjust``; they are migrated in place and legacy
    rows are conservatively labelled ``qfq`` because that was the old default.
    """

    _KLINE_COLUMNS = ["datetime", "open", "high", "low", "close", "volume", "amount", "code", "adjust"]

    def __init__(self, path: str | Path = "market.duckdb"):
        self.path = str(path)

    @staticmethod
    def _utcnow_naive() -> pd.Timestamp:
        # DuckDB tables intentionally store UTC wall time without timezone.
        # Timestamp.utcnow() is deprecated in pandas 3/4; normalize explicitly.
        return pd.Timestamp.now(tz="UTC").tz_localize(None)

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

    @staticmethod
    def _table_columns(con, table: str) -> set[str]:
        try:
            rows = con.execute(f"PRAGMA table_info('{table}')").fetchall()
        except Exception:
            return set()
        # DuckDB PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
        return {str(row[1]) for row in rows}

    def _ensure_kline_table(self, con, table: str) -> None:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                datetime TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                amount DOUBLE,
                code VARCHAR,
                adjust VARCHAR
            )
            """
        )
        columns = self._table_columns(con, table)
        if "adjust" not in columns:
            con.execute(f"ALTER TABLE {table} ADD COLUMN adjust VARCHAR")
            con.execute(f"UPDATE {table} SET adjust = 'qfq' WHERE adjust IS NULL")

    @staticmethod
    def _ensure_coverage_table(con) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS history_coverage (
                code VARCHAR,
                interval VARCHAR,
                adjust VARCHAR,
                covered_start TIMESTAMP,
                covered_end TIMESTAMP,
                updated_at TIMESTAMP
            )
            """
        )
        try:
            columns = {str(row[1]) for row in con.execute("PRAGMA table_info('history_coverage')").fetchall()}
        except Exception:
            columns = set()
        if "adjust" not in columns:
            con.execute("ALTER TABLE history_coverage ADD COLUMN adjust VARCHAR")
            con.execute("UPDATE history_coverage SET adjust = 'qfq' WHERE adjust IS NULL")

    def save_stock_list(self, stocks: pd.DataFrame, provider: str = "") -> None:
        if stocks is None or stocks.empty:
            return
        temp = stocks.copy()
        temp["code"] = temp["code"].astype(str).str.zfill(6)
        temp["provider"] = provider
        temp["updated_at"] = self._utcnow_naive()
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
        now = self._utcnow_naive()
        return max(0.0, float((now - updated).total_seconds() / 3600.0))

    def save_history(self, code: str, interval: str, df: pd.DataFrame, adjust: str = "qfq") -> None:
        if df is None or df.empty:
            return
        table = self._table(interval)
        temp = df.copy()
        required = ["datetime", "open", "high", "low", "close", "volume", "amount"]
        missing = [column for column in required if column not in temp.columns]
        if missing:
            raise ValueError(f"history missing normalized columns: {missing}")
        temp = temp[required].copy()
        temp["code"] = str(code).zfill(6)
        temp["adjust"] = str(adjust or "none")
        temp["datetime"] = pd.to_datetime(temp["datetime"])
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            temp[column] = pd.to_numeric(temp[column], errors="coerce")
        temp = temp.dropna(subset=["datetime", "open", "high", "low", "close"])
        if temp.empty:
            return
        with self._connect() as con:
            self._ensure_kline_table(con, table)
            start = temp["datetime"].min()
            end = temp["datetime"].max()
            con.execute(
                f"DELETE FROM {table} WHERE code = ? AND adjust = ? AND datetime BETWEEN ? AND ?",
                [str(code).zfill(6), str(adjust or "none"), start, end],
            )
            con.register("temp_history", temp[self._KLINE_COLUMNS])
            con.execute(
                f"""
                INSERT INTO {table} (datetime, open, high, low, close, volume, amount, code, adjust)
                SELECT datetime, open, high, low, close, volume, amount, code, adjust FROM temp_history
                """
            )
            con.unregister("temp_history")

    def load_history(
        self,
        code: str,
        interval: str,
        start: date | str | pd.Timestamp | None = None,
        end: date | str | pd.Timestamp | None = None,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        table = self._table(interval)
        code = str(code).zfill(6)
        adjust = str(adjust or "none")
        clauses = ["code = ?", "adjust = ?"]
        params: list[object] = [code, adjust]
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
                self._ensure_kline_table(con, table)
                return con.execute(
                    f"SELECT datetime, open, high, low, close, volume, amount, code, adjust FROM {table} WHERE {where} ORDER BY datetime",
                    params,
                ).df()
            except Exception:
                return pd.DataFrame()

    def history_bounds(self, code: str, interval: str, adjust: str = "qfq") -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        table = self._table(interval)
        with self._connect() as con:
            try:
                self._ensure_kline_table(con, table)
                row = con.execute(
                    f"SELECT min(datetime), max(datetime) FROM {table} WHERE code = ? AND adjust = ?",
                    [str(code).zfill(6), str(adjust or "none")],
                ).fetchone()
            except Exception:
                return None, None
        if not row or row[0] is None:
            return None, None
        return pd.Timestamp(row[0]), pd.Timestamp(row[1])

    def clear_history(self, code: str, interval: str, adjust: str = "qfq") -> None:
        table = self._table(interval)
        code = str(code).zfill(6)
        adjust = str(adjust or "none")
        with self._connect() as con:
            try:
                self._ensure_kline_table(con, table)
                con.execute(f"DELETE FROM {table} WHERE code = ? AND adjust = ?", [code, adjust])
            except Exception:
                pass
            try:
                self._ensure_coverage_table(con)
                con.execute(
                    "DELETE FROM history_coverage WHERE code = ? AND interval = ? AND adjust = ?",
                    [code, interval, adjust],
                )
            except Exception:
                pass

    def mark_history_coverage(
        self,
        code: str,
        interval: str,
        start: date | str | pd.Timestamp,
        end: date | str | pd.Timestamp,
        adjust: str = "qfq",
    ) -> None:
        code = str(code).zfill(6)
        adjust = str(adjust or "none")
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize()
        with self._connect() as con:
            self._ensure_coverage_table(con)
            row = con.execute(
                "SELECT covered_start, covered_end FROM history_coverage WHERE code = ? AND interval = ? AND adjust = ?",
                [code, interval, adjust],
            ).fetchone()
            if row:
                start_ts = min(start_ts, pd.Timestamp(row[0]))
                end_ts = max(end_ts, pd.Timestamp(row[1]))
                con.execute(
                    "DELETE FROM history_coverage WHERE code = ? AND interval = ? AND adjust = ?",
                    [code, interval, adjust],
                )
            con.execute(
                "INSERT INTO history_coverage (code, interval, adjust, covered_start, covered_end, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                [code, interval, adjust, start_ts, end_ts, self._utcnow_naive()],
            )

    def coverage_bounds(self, code: str, interval: str, adjust: str = "qfq") -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        with self._connect() as con:
            try:
                self._ensure_coverage_table(con)
                row = con.execute(
                    "SELECT covered_start, covered_end FROM history_coverage WHERE code = ? AND interval = ? AND adjust = ?",
                    [str(code).zfill(6), interval, str(adjust or "none")],
                ).fetchone()
            except Exception:
                return None, None
        if not row:
            return None, None
        return pd.Timestamp(row[0]), pd.Timestamp(row[1])

    def save_scores(self, scores: pd.DataFrame) -> None:
        if scores is None or scores.empty:
            return
        temp = scores.copy()
        temp["scored_at"] = self._utcnow_naive()
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
