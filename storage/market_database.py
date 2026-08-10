from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
import uuid

import pandas as pd

from storage.duckdb_store import DuckDBStore


@dataclass(frozen=True)
class MarketDatabaseStats:
    path: str
    file_size_mb: float
    universe_rows: int
    sh_rows: int
    sz_rows: int
    bj_rows: int
    raw_daily_rows: int
    raw_daily_codes: int
    raw_start: str
    raw_end: str
    qfq_daily_rows: int
    qfq_daily_codes: int
    qfq_start: str
    qfq_end: str
    last_universe_update: str
    last_data_update: str

    def to_dict(self) -> dict:
        return asdict(self)


class MarketDatabase:
    """Higher-level schema around the existing DuckDB K-line store.

    ``DuckDBStore`` remains the normalized bar cache. This class adds durable
    security master/snapshot metadata, update audit records, adjustment-factor
    storage and scan audit records. Normal application reads stay local-only.
    """

    def __init__(self, path: str | Path = "market.duckdb"):
        self.path = str(path)
        self.store = DuckDBStore(self.path)
        self.ensure_schema()

    def _connect(self):
        return self.store._connect()

    def ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS security_master (
                    code VARCHAR,
                    market VARCHAR,
                    name VARCHAR,
                    listing_date DATE,
                    delist_date DATE,
                    status VARCHAR,
                    source VARCHAR,
                    updated_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS security_snapshots (
                    as_of DATE,
                    code VARCHAR,
                    market VARCHAR,
                    name VARCHAR,
                    trade_status VARCHAR,
                    source VARCHAR,
                    captured_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS adjustment_factors (
                    code VARCHAR,
                    trade_date DATE,
                    adj_factor DOUBLE,
                    source VARCHAR,
                    updated_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS data_update_runs (
                    run_id VARCHAR,
                    run_type VARCHAR,
                    started_at TIMESTAMP,
                    finished_at TIMESTAMP,
                    status VARCHAR,
                    requested_codes INTEGER,
                    raw_rows BIGINT,
                    qfq_rows BIGINT,
                    failures INTEGER,
                    message VARCHAR
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_runs (
                    run_id VARCHAR,
                    scanned_at TIMESTAMP,
                    universe_rows INTEGER,
                    first_stage_rows INTEGER,
                    deep_rows INTEGER,
                    valid_score_rows INTEGER,
                    final_rows INTEGER,
                    parameters VARCHAR,
                    note VARCHAR
                )
                """
            )

    @staticmethod
    def _infer_market(code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith(("92", "8", "4")):
            return "BJ"
        if code.startswith(("5", "6")):
            return "SH"
        return "SZ"

    def sync_universe(self, stocks: pd.DataFrame, source: str = "") -> int:
        if stocks is None or stocks.empty:
            return 0
        now = self.store._utcnow_naive()
        x = stocks.copy()
        x["code"] = x["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        if "market" not in x.columns:
            x["market"] = x["code"].map(self._infer_market)
        x["market"] = x["market"].astype(str).str.upper()
        if "name" not in x.columns:
            x["name"] = ""
        x["name"] = x["name"].astype(str)
        if "listing_date" in x.columns:
            x["listing_date"] = pd.to_datetime(x["listing_date"], errors="coerce").dt.date
        else:
            x["listing_date"] = pd.NaT
        if "delist_date" in x.columns:
            x["delist_date"] = pd.to_datetime(x["delist_date"], errors="coerce").dt.date
        else:
            x["delist_date"] = pd.NaT
        if "trade_status" in x.columns:
            x["status"] = x["trade_status"].astype(str)
        elif "status" not in x.columns:
            x["status"] = "active"
        x["source"] = str(source or stocks.attrs.get("provider") or "unknown")
        x["updated_at"] = now
        x = x.drop_duplicates(["market", "code"], keep="last").reset_index(drop=True)

        # Keep the legacy/current-universe table in sync for existing code.
        self.store.save_stock_list(x[[c for c in stocks.columns if c in x.columns] + [c for c in ["market", "name"] if c not in stocks.columns]], provider=x["source"].iloc[0])

        master = x[["code", "market", "name", "listing_date", "delist_date", "status", "source", "updated_at"]].copy()
        snap = x[["code", "market", "name", "status", "source"]].rename(columns={"status": "trade_status"})
        snap["as_of"] = pd.Timestamp(date.today()).date()
        snap["captured_at"] = now

        with self._connect() as con:
            con.register("temp_master", master)
            con.execute("DELETE FROM security_master WHERE code IN (SELECT code FROM temp_master)")
            con.execute("INSERT INTO security_master SELECT * FROM temp_master")
            con.unregister("temp_master")

            con.execute("DELETE FROM security_snapshots WHERE as_of = ?", [pd.Timestamp(date.today()).date()])
            con.register("temp_snapshot", snap[["as_of", "code", "market", "name", "trade_status", "source", "captured_at"]])
            con.execute("INSERT INTO security_snapshots SELECT * FROM temp_snapshot")
            con.unregister("temp_snapshot")
        return len(x)

    def save_adjustment_factors(self, factors: pd.DataFrame, source: str = "") -> int:
        """Persist provider-supplied factors when a provider exposes them.

        The current application may still materialize qfq bars for fast scoring;
        raw bars remain the durable price source of truth. This table is ready
        for providers that expose explicit adjustment factors.
        """
        if factors is None or factors.empty:
            return 0
        required = {"code", "trade_date", "adj_factor"}
        if not required.issubset(factors.columns):
            raise ValueError(f"adjustment factors missing columns: {sorted(required - set(factors.columns))}")
        x = factors.copy()
        x["code"] = x["code"].astype(str).str.zfill(6)
        x["trade_date"] = pd.to_datetime(x["trade_date"], errors="coerce").dt.date
        x["adj_factor"] = pd.to_numeric(x["adj_factor"], errors="coerce")
        x = x.dropna(subset=["trade_date", "adj_factor"])
        x["source"] = str(source or "unknown")
        x["updated_at"] = self.store._utcnow_naive()
        with self._connect() as con:
            con.register("temp_factors", x[["code", "trade_date", "adj_factor", "source", "updated_at"]])
            con.execute(
                """
                DELETE FROM adjustment_factors
                WHERE (code, trade_date) IN (SELECT code, trade_date FROM temp_factors)
                """
            )
            con.execute("INSERT INTO adjustment_factors SELECT * FROM temp_factors")
            con.unregister("temp_factors")
        return len(x)

    def latest_daily_snapshot(self, adjust: str = "none") -> pd.DataFrame:
        """Build a spot-like cross section from the latest two local daily bars."""
        adjust = str(adjust or "none")
        with self._connect() as con:
            try:
                self.store._ensure_kline_table(con, "kline_day")
                out = con.execute(
                    """
                    WITH ranked AS (
                        SELECT
                            code, datetime, open, high, low, close, amount,
                            row_number() OVER (PARTITION BY code ORDER BY datetime DESC) AS rn
                        FROM kline_day
                        WHERE adjust = ?
                    ),
                    cur AS (
                        SELECT code, datetime, open, high, low, close, amount
                        FROM ranked WHERE rn = 1
                    ),
                    prev AS (
                        SELECT code, close AS prev_close
                        FROM ranked WHERE rn = 2
                    )
                    SELECT
                        cur.code,
                        coalesce(s.name, '') AS name,
                        coalesce(s.market, '') AS market,
                        cur.close AS price,
                        prev.prev_close,
                        cur.open,
                        CASE WHEN prev.prev_close > 0
                             THEN (cur.close / prev.prev_close - 1) * 100
                             ELSE NULL END AS pct_change,
                        cur.high,
                        cur.low,
                        CASE WHEN prev.prev_close > 0
                             THEN (cur.high - cur.low) / prev.prev_close * 100
                             ELSE NULL END AS amplitude,
                        cur.amount,
                        CAST(NULL AS DOUBLE) AS turnover,
                        cur.datetime AS quote_time,
                        'local-latest-daily' AS source
                    FROM cur
                    LEFT JOIN prev USING (code)
                    LEFT JOIN stock_universe s USING (code)
                    ORDER BY cur.code
                    """,
                    [adjust],
                ).df()
            except Exception:
                return pd.DataFrame()
        if not out.empty:
            out["code"] = out["code"].astype(str).str.zfill(6)
            out.attrs["provider"] = "local-latest-daily"
        return out

    def begin_update_run(self, run_type: str, requested_codes: int) -> str:
        run_id = uuid.uuid4().hex[:16]
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO data_update_runs
                (run_id, run_type, started_at, status, requested_codes, raw_rows, qfq_rows, failures, message)
                VALUES (?, ?, ?, 'running', ?, 0, 0, 0, '')
                """,
                [run_id, run_type, self.store._utcnow_naive(), int(requested_codes)],
            )
        return run_id

    def finish_update_run(
        self,
        run_id: str,
        *,
        status: str,
        raw_rows: int = 0,
        qfq_rows: int = 0,
        failures: int = 0,
        message: str = "",
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                UPDATE data_update_runs
                SET finished_at = ?, status = ?, raw_rows = ?, qfq_rows = ?, failures = ?, message = ?
                WHERE run_id = ?
                """,
                [self.store._utcnow_naive(), status, int(raw_rows), int(qfq_rows), int(failures), str(message), run_id],
            )

    def record_scan_run(
        self,
        *,
        universe_rows: int,
        first_stage_rows: int,
        deep_rows: int,
        valid_score_rows: int,
        final_rows: int,
        parameters: str = "",
        note: str = "",
    ) -> str:
        run_id = uuid.uuid4().hex[:16]
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO scan_runs
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    self.store._utcnow_naive(),
                    int(universe_rows),
                    int(first_stage_rows),
                    int(deep_rows),
                    int(valid_score_rows),
                    int(final_rows),
                    str(parameters),
                    str(note),
                ],
            )
        return run_id

    def _bar_stats(self, adjust: str) -> tuple[int, int, str, str]:
        with self._connect() as con:
            try:
                self.store._ensure_kline_table(con, "kline_day")
                row = con.execute(
                    """
                    SELECT count(*), count(DISTINCT code), min(datetime), max(datetime)
                    FROM kline_day WHERE adjust = ?
                    """,
                    [adjust],
                ).fetchone()
            except Exception:
                row = None
        if not row:
            return 0, 0, "", ""
        return (
            int(row[0] or 0),
            int(row[1] or 0),
            "" if row[2] is None else pd.Timestamp(row[2]).date().isoformat(),
            "" if row[3] is None else pd.Timestamp(row[3]).date().isoformat(),
        )

    def stats(self) -> MarketDatabaseStats:
        stocks = self.store.load_stock_list()
        universe_rows = 0 if stocks is None else len(stocks)
        market_counts = {}
        if stocks is not None and not stocks.empty and "market" in stocks.columns:
            market_counts = stocks["market"].astype(str).str.upper().value_counts().to_dict()

        raw_rows, raw_codes, raw_start, raw_end = self._bar_stats("none")
        qfq_rows, qfq_codes, qfq_start, qfq_end = self._bar_stats("qfq")

        last_universe = ""
        if stocks is not None and not stocks.empty and "updated_at" in stocks.columns:
            values = pd.to_datetime(stocks["updated_at"], errors="coerce").dropna()
            if not values.empty:
                last_universe = values.max().isoformat(sep=" ", timespec="seconds")

        last_data = ""
        with self._connect() as con:
            try:
                row = con.execute(
                    "SELECT max(finished_at) FROM data_update_runs WHERE status = 'success'"
                ).fetchone()
                if row and row[0] is not None:
                    last_data = pd.Timestamp(row[0]).isoformat(sep=" ", timespec="seconds")
            except Exception:
                pass

        p = Path(self.path)
        size_mb = round(p.stat().st_size / 1024 / 1024, 2) if p.exists() else 0.0
        return MarketDatabaseStats(
            path=self.path,
            file_size_mb=size_mb,
            universe_rows=universe_rows,
            sh_rows=int(market_counts.get("SH", 0)),
            sz_rows=int(market_counts.get("SZ", 0)),
            bj_rows=int(market_counts.get("BJ", 0)),
            raw_daily_rows=raw_rows,
            raw_daily_codes=raw_codes,
            raw_start=raw_start,
            raw_end=raw_end,
            qfq_daily_rows=qfq_rows,
            qfq_daily_codes=qfq_codes,
            qfq_start=qfq_start,
            qfq_end=qfq_end,
            last_universe_update=last_universe,
            last_data_update=last_data,
        )
