from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd

from .base import MarketDataError, MarketDataProvider, NoMarketData


class BaostockHistoryProvider(MarketDataProvider):
    """Independent BaoStock fallback for SH/SZ daily and minute K-lines.

    BaoStock 0.9.3 exposes daily plus 5/15/30/60 minute history. BSE coverage is
    deliberately not claimed here; that remains a separate source-validation
    problem rather than being hidden behind code-prefix guessing.
    """

    name = "baostock-history"

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
            raise NoMarketData("BaoStock history fallback does not claim BSE coverage")
        return ("sh." if code.startswith(("5", "6", "9")) else "sz.") + code

    @staticmethod
    def _date(value: date | str) -> str:
        return pd.Timestamp(value).strftime("%Y-%m-%d")

    @staticmethod
    def _adjust_flag(adjust: str) -> str:
        # BaoStock: 1=后复权, 2=前复权, 3=不复权
        return {"hfq": "1", "qfq": "2", "none": "3", "": "3"}.get(str(adjust).lower(), "2")

    @staticmethod
    def _frequency(interval: str) -> str:
        mapping = {"1d": "d", "day": "d", "5m": "5", "15m": "15", "30m": "30", "60m": "60"}
        try:
            return mapping[str(interval).lower()]
        except KeyError as exc:
            raise NoMarketData(f"BaoStock fallback does not support {interval}") from exc

    @staticmethod
    def _fields(frequency: str) -> str:
        if frequency == "d":
            return "date,code,open,high,low,close,volume,amount,turn,tradestatus,pctChg,isST"
        return "date,time,code,open,high,low,close,volume,amount,adjustflag"

    @staticmethod
    def _result_frame(result, operation: str) -> pd.DataFrame:
        if getattr(result, "error_code", "0") != "0":
            raise MarketDataError(f"BaoStock {operation} failed: {getattr(result, 'error_msg', '')}")
        rows: list[list[str]] = []
        while result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=getattr(result, "fields", None))

    def _query_source_code(
        self,
        source_code: str,
        start: date | str,
        end: date | str,
        *,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        frequency = self._frequency(interval)
        fields = self._fields(frequency)
        with self._session() as bs:
            result = bs.query_history_k_data_plus(
                source_code,
                fields,
                start_date=self._date(start),
                end_date=self._date(end),
                frequency=frequency,
                adjustflag=self._adjust_flag(adjust),
            )
            raw = self._result_frame(result, "query_history_k_data_plus")
        if raw.empty:
            raise NoMarketData(f"BaoStock returned no {interval} rows for {source_code}")
        df = raw.copy()
        if frequency == "d":
            df = df.rename(columns={"date": "datetime", "turn": "turnover", "pctChg": "pct_change"})
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        else:
            # BaoStock minute time is YYYYMMDDHHMMSSsss. Milliseconds are not
            # needed for 5/15/30/60m bars; keep the first 14 digits.
            text = df["time"].astype(str).str.slice(0, 14)
            df["datetime"] = pd.to_datetime(text, format="%Y%m%d%H%M%S", errors="coerce")
        numeric = ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_change"]
        for col in numeric:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
        if df.empty:
            raise NoMarketData(f"BaoStock {interval} rows for {source_code} were not parseable")
        df.attrs["provider"] = self.name
        df.attrs["source_code"] = source_code
        return df

    def history(
        self,
        code: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        code = str(code).zfill(6)
        df = self._query_source_code(self._source_code(code), start, end, interval=interval, adjust=adjust)
        df.attrs["code"] = code
        return df

    def stock_list(self) -> pd.DataFrame:
        # Security identity belongs to SecurityMasterProvider. Do not return an
        # SH/SZ-only list here because ProviderChain could mistake it for a full
        # SH/SZ/BJ current universe.
        raise MarketDataError("BaoStock history provider intentionally does not implement stock_list")


# Backward-compatible import name used by the live gate before minute support
# was enabled. New code should prefer BaostockHistoryProvider.
BaostockDailyProvider = BaostockHistoryProvider
