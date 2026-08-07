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
        # BaoStock's documented examples consume ResultData via next() and
        # get_row_data(). Prefer that protocol; get_data() is only a fallback
        # for wrappers/fakes because its behavior has varied across versions.
        if hasattr(result, "next") and hasattr(result, "get_row_data"):
            rows: list[list[str]] = []
            while result.next():
                rows.append(result.get_row_data())
            if rows:
                return pd.DataFrame(rows, columns=getattr(result, "fields", None))
        if hasattr(result, "get_data"):
            data = result.get_data()
            return data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        return pd.DataFrame(columns=getattr(result, "fields", None))

    def _trade_dates_through(self, bs, as_of: pd.Timestamp) -> list[pd.Timestamp]:
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
        return [pd.Timestamp(x).normalize() for x in sorted(trading["calendar_date"].unique(), reverse=True)]

    def _latest_available_snapshot_raw(self, bs, as_of: pd.Timestamp) -> tuple[pd.Timestamp, pd.DataFrame]:
        # query_all_stock is documented as updating together with daily K data.
        # During an open session the calendar already marks today as trading,
        # while today's snapshot can still be unpublished. Only then walk back.
        for trade_date in self._trade_dates_through(bs, as_of):
            raw = self._get_data(bs.query_all_stock(day=trade_date.strftime("%Y-%m-%d")), "query_all_stock")
            if not raw.empty:
                return trade_date, raw
        raise RuntimeError(
            f"BaoStock returned no security snapshot within {self.lookback_calendar_days} calendar days through {as_of.strftime('%Y-%m-%d')}"
        )

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
            trade_date, raw = self._latest_available_snapshot_raw(bs, requested)
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
            if cal.empty:
                raise RuntimeError(f"BaoStock returned no trading calendar through {end}")
            cal["calendar_date"] = pd.to_datetime(cal["calendar_date"], errors="coerce")
            trading = pd.DatetimeIndex(cal.loc[cal["is_trading_day"].astype(str) == "1", "calendar_date"].dropna().sort_values().unique())
            trade_values = trading.to_numpy(dtype="datetime64[ns]")
            raw_cache: dict[pd.Timestamp, pd.DataFrame] = {}
            resolved: set[pd.Timestamp] = set()
            for req in requested:
                pos = int(trade_values.searchsorted(np.datetime64(req), side="right")) - 1
                if pos < 0:
                    continue
                chosen_day = None
                chosen_raw = None
                for idx in range(pos, -1, -1):
                    candidate = pd.Timestamp(trade_values[idx]).normalize()
                    if (req - candidate).days > self.lookback_calendar_days:
                        break
                    if candidate not in raw_cache:
                        raw_cache[candidate] = self._get_data(
                            bs.query_all_stock(day=candidate.strftime("%Y-%m-%d")), "query_all_stock"
                        )
                    if not raw_cache[candidate].empty:
                        chosen_day = candidate
                        chosen_raw = raw_cache[candidate]
                        break
                if chosen_day is None or chosen_raw is None or chosen_day in resolved:
                    continue
                resolved.add(chosen_day)
                frame = self._normalize_snapshot(chosen_raw, chosen_day, self.name)
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
