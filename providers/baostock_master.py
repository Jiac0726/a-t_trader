from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import numpy as np
import pandas as pd

from providers.security_master import (
    MASTER_COLUMNS,
    SNAPSHOT_COLUMNS,
    SecurityMasterProvider,
    looks_like_a_share_stock,
    normalize_exchange_code,
)


class BaostockSecurityMasterProvider(SecurityMasterProvider):
    """Optional BaoStock point-in-time security master."""

    def __init__(self, lookback_calendar_days: int = 14):
        self.lookback_calendar_days = max(3, int(lookback_calendar_days))

    @property
    def name(self) -> str:
        return "baostock-security-master"

    @staticmethod
    def _bs():
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError("BaoStock is optional. Install with: pip install 'a-t-trader[baostock]' or pip install baostock>=0.9.3") from exc
        return bs

    @contextmanager
    def _session(self):
        bs = self._bs()
        login = bs.login()
        if getattr(login, "error_code", "0") != "0":
            raise RuntimeError(f"BaoStock login failed: {getattr(login, 'error_msg', '')}")
        try:
            yield bs
        finally:
            try:
                bs.logout()
            except Exception:
                pass

    @staticmethod
    def _get_data(result, operation: str) -> pd.DataFrame:
        if getattr(result, "error_code", "0") != "0":
            raise RuntimeError(f"BaoStock {operation} failed: {getattr(result, 'error_msg', '')}")
        if hasattr(result, "get_data"):
            data = result.get_data()
            return data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        rows: list[list[str]] = []
        while result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=getattr(result, "fields", None))

    def _resolve_trade_date(self, bs, as_of: pd.Timestamp) -> pd.Timestamp:
        start = (as_of - timedelta(days=self.lookback_calendar_days)).strftime("%Y-%m-%d")
        end = as_of.strftime("%Y-%m-%d")
        cal = self._get_data(bs.query_trade_dates(start_date=start, end_date=end), "query_trade_dates")
        if cal.empty:
            raise RuntimeError(f"BaoStock returned no trading calendar through {end}")
        cal["calendar_date"] = pd.to_datetime(cal["calendar_date"], errors="coerce")
        trading = cal[cal["is_trading_day"].astype(str) == "1"].dropna(subset=["calendar_date"])
        trading = trading[trading["calendar_date"] <= as_of]
        if trading.empty:
            raise RuntimeError(f"No trading day found within {self.lookback_calendar_days} calendar days before {end}")
        return pd.Timestamp(trading["calendar_date"].max()).normalize()

    @staticmethod
    def _normalize_snapshot(raw: pd.DataFrame, trade_date: pd.Timestamp, source: str) -> pd.DataFrame:
        rows: list[dict] = []
        for row in raw.to_dict("records"):
            exchange, code = normalize_exchange_code(row.get("code", ""))
            if not looks_like_a_share_stock(exchange, code):
                continue
            value = str(row.get("tradeStatus", "0") or "0")
            rows.append({
                "as_of": trade_date,
                "code": code,
                "exchange": exchange,
                "name": str(row.get("code_name", "") or ""),
                "trade_status": int(value) if value.isdigit() else 0,
                "source_code": str(row.get("code", "") or ""),
                "source": source,
            })
        return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)

    def snapshot(self, as_of) -> pd.DataFrame:
        requested = pd.Timestamp(as_of).normalize()
        with self._session() as bs:
            trade_date = self._resolve_trade_date(bs, requested)
            raw = self._get_data(bs.query_all_stock(day=trade_date.strftime("%Y-%m-%d")), "query_all_stock")
        out = self._normalize_snapshot(raw, trade_date, self.name)
        if out.empty:
            return out
        out.attrs["requested_as_of"] = requested.strftime("%Y-%m-%d")
        out.attrs["resolved_trade_date"] = trade_date.strftime("%Y-%m-%d")
        out.attrs["coverage_note"] = "SH/SZ stock filtering is conservative; BJ is best-effort by code prefix because BaoStock basic metadata coverage may differ."
        return out.drop_duplicates(["exchange", "code"], keep="last").sort_values(["exchange", "code"]).reset_index(drop=True)

    def snapshot_many(self, as_of_dates) -> pd.DataFrame:
        requested = sorted({pd.Timestamp(x).normalize() for x in as_of_dates})
        if not requested:
            return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
        parts: list[pd.DataFrame] = []
        with self._session() as bs:
            start = (requested[0] - timedelta(days=self.lookback_calendar_days)).strftime("%Y-%m-%d")
            end = requested[-1].strftime("%Y-%m-%d")
            cal = self._get_data(bs.query_trade_dates(start_date=start, end_date=end), "query_trade_dates")
            cal["calendar_date"] = pd.to_datetime(cal["calendar_date"], errors="coerce")
            trading = pd.DatetimeIndex(cal.loc[cal["is_trading_day"].astype(str) == "1", "calendar_date"].dropna().sort_values().unique())
            trade_values = trading.to_numpy(dtype="datetime64[ns]")
            resolved: set[pd.Timestamp] = set()
            for req in requested:
                pos = int(trade_values.searchsorted(np.datetime64(req), side="right")) - 1
                if pos >= 0:
                    resolved.add(pd.Timestamp(trade_values[pos]).normalize())
            for trade_date in sorted(resolved):
                raw = self._get_data(bs.query_all_stock(day=trade_date.strftime("%Y-%m-%d")), "query_all_stock")
                frame = self._normalize_snapshot(raw, trade_date, self.name)
                if not frame.empty:
                    parts.append(frame)
        out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=SNAPSHOT_COLUMNS)
        if not out.empty:
            out = out.drop_duplicates(["as_of", "exchange", "code"], keep="last").sort_values(["as_of", "exchange", "code"]).reset_index(drop=True)
            out.attrs["coverage_note"] = "SH/SZ stock filtering is conservative; BJ is best-effort by code prefix."
        return out

    def master(self) -> pd.DataFrame:
        with self._session() as bs:
            raw = self._get_data(bs.query_stock_basic(), "query_stock_basic")
        if raw.empty:
            return pd.DataFrame(columns=MASTER_COLUMNS)
        rows: list[dict] = []
        for row in raw.to_dict("records"):
            if str(row.get("type", "") or "") != "1":
                continue
            exchange, code = normalize_exchange_code(row.get("code", ""))
            if exchange not in {"SH", "SZ", "BJ"}:
                continue
            status = str(row.get("status", "0") or "0")
            rows.append({
                "code": code,
                "exchange": exchange,
                "name": str(row.get("code_name", "") or ""),
                "ipo_date": pd.to_datetime(row.get("ipoDate", ""), errors="coerce"),
                "delist_date": pd.to_datetime(row.get("outDate", ""), errors="coerce"),
                "security_type": "stock",
                "listed_status": int(status) if status.isdigit() else 0,
                "source_code": str(row.get("code", "") or ""),
                "source": self.name,
            })
        return pd.DataFrame(rows, columns=MASTER_COLUMNS).drop_duplicates(["exchange", "code"], keep="last").sort_values(["exchange", "code"]).reset_index(drop=True)
