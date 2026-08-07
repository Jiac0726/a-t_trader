from __future__ import annotations

from datetime import date
import time
from typing import Any

import pandas as pd
import requests

from .base import MarketDataError, MarketDataProvider


class EastmoneyProvider(MarketDataProvider):
    """Direct Eastmoney K-line adapter.

    This keeps the market-data layer replaceable. Public endpoints can change or
    rate-limit, so this provider must never be assumed to be permanently stable.
    """

    name = "eastmoney-direct"
    _URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def __init__(self, timeout: float = 12.0, min_interval: float = 0.35):
        self.timeout = timeout
        self.min_interval = min_interval
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
                "Referer": "https://quote.eastmoney.com/",
            }
        )

    @staticmethod
    def _secid(code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith(("5", "6", "9")):
            return f"1.{code}"
        return f"0.{code}"

    @staticmethod
    def _date_str(value: date | str) -> str:
        if isinstance(value, date):
            return value.strftime("%Y%m%d")
        return str(value).replace("-", "")[:8]

    @staticmethod
    def _klt(interval: str) -> str:
        mapping = {"1d": "101", "day": "101", "5m": "5", "15m": "15", "30m": "30", "60m": "60"}
        try:
            return mapping[interval]
        except KeyError as exc:
            raise ValueError(f"Unsupported interval: {interval}") from exc

    @staticmethod
    def _fqt(adjust: str) -> str:
        return {"none": "0", "": "0", "qfq": "1", "hfq": "2"}.get(adjust, "1")

    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def history(self, code: str, start: date | str, end: date | str, interval: str = "1d", adjust: str = "qfq") -> pd.DataFrame:
        code = str(code).zfill(6)
        params: dict[str, Any] = {
            "secid": self._secid(code),
            "klt": self._klt(interval),
            "fqt": self._fqt(adjust),
            "beg": self._date_str(start),
            "end": self._date_str(end),
            "lmt": "1000000",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        self._throttle()
        try:
            response = self.session.get(self._URL, params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise MarketDataError(f"Eastmoney request failed for {code}: {exc}") from exc

        data = payload.get("data") or {}
        klines = data.get("klines") or []
        if not klines:
            raise MarketDataError(f"Eastmoney returned no K-line data for {code}")

        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 11:
                continue
            rows.append(
                {
                    "datetime": parts[0],
                    "open": parts[1],
                    "close": parts[2],
                    "high": parts[3],
                    "low": parts[4],
                    "volume": parts[5],
                    "amount": parts[6],
                    "amplitude": parts[7],
                    "pct_change": parts[8],
                    "change": parts[9],
                    "turnover": parts[10],
                }
            )
        df = pd.DataFrame(rows)
        if df.empty:
            raise MarketDataError(f"Eastmoney payload could not be parsed for {code}")

        numeric = ["open", "close", "high", "low", "volume", "amount", "amplitude", "pct_change", "change", "turnover"]
        for col in numeric:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
        df.attrs["name"] = data.get("name", "")
        df.attrs["code"] = data.get("code", code)
        df.attrs["provider"] = self.name
        return df

    def stock_name(self, code: str) -> str:
        # Name is returned with K-line payload; resolving it separately would add another endpoint.
        return ""
