from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd

from .base import MarketDataError, MarketDataProvider, NoMarketData


class BaostockDailyProvider(MarketDataProvider):
    """Optional independent SH/SZ daily-K fallback backed by BaoStock.

    BaoStock is deliberately used only for daily bars here. It does not become
    the minute-data provider, and current BSE coverage is not claimed by this
    adapter. That keeps provider capabilities explicit instead of silently
    pretending all sources have identical market coverage.
    """

    name = "baostock-daily"

    @staticmethod
    def _bs():
        try:
            import baostock as bs
        except ImportError as exc:
            raise MarketDataError("BaoStock is optional; install baostock>=0.9.3") from exc
        return bs

    @contextmanager
    def _session(self):
        bs = self._bs()
        login = bs.login()
        if getattr(login, "error_code", "0") != "0":
            raise MarketDataError(f"BaoStock login failed: {getattr(login, 'error_msg', '')}")
        try:
            yield bs
        finally:
            try:
                bs.logout()
            except Exception:
                pass

    @staticmethod
    def _source_code(code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith(("4", "8", "92")):
            raise NoMarketData("BaoStock daily fallback does not claim BSE coverage")
        return ("sh." if code.startswith(("5", "6", "9")) else "sz.") + code

    @staticmethod
    def _date(value: date | str) -> str:
        return pd.Timestamp(value).strftime("%Y-%m-%d")

    @staticmethod
    def _adjust_flag(adjust: str) -> str:
        # BaoStock: 1=后复权, 2=前复权, 3=不复权
        return {"hfq": "1", "qfq": "2", "none": "3", "": "3"}.get(str(adjust).lower(), "2")

    @staticmethod
    def _result_frame(result, operation: str) -> pd.DataFrame:
        if getattr(result, "error_code", "0") != "0":
            raise MarketDataError(f"BaoStock {operation} failed: {getattr(result, 'error_msg', '')}")
        rows: list[list[str]] = []
        while result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=getattr(result, "fields", None))

    def history(
        self,
        code: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        if interval not in {"1d", "day"}:
            raise NoMarketData(f"BaoStock fallback supports daily bars only, not {interval}")
        source_code = self._source_code(code)
        fields = "date,code,open,high,low,close,volume,amount,turn,tradestatus,pctChg,isST"
        with self._session() as bs:
            result = bs.query_history_k_data_plus(
                source_code,
                fields,
                start_date=self._date(start),
                end_date=self._date(end),
                frequency="d",
                adjustflag=self._adjust_flag(adjust),
            )
            raw = self._result_frame(result, "query_history_k_data_plus")
        if raw.empty:
            raise NoMarketData(f"BaoStock returned no daily rows for {code}")
        rename = {"date": "datetime", "turn": "turnover", "pctChg": "pct_change"}
        df = raw.rename(columns=rename).copy()
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        numeric = ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_change"]
        for col in numeric:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
        if df.empty:
            raise NoMarketData(f"BaoStock daily rows for {code} were not parseable")
        df.attrs["code"] = str(code).zfill(6)
        df.attrs["provider"] = self.name
        return df

    def stock_list(self) -> pd.DataFrame:
        # Security identity belongs to SecurityMasterProvider. Do not return an
        # SH/SZ-only list here because ProviderChain could mistake it for a full
        # SH/SZ/BJ current universe.
        raise MarketDataError("BaoStock daily provider intentionally does not implement stock_list")
