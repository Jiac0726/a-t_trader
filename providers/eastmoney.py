from __future__ import annotations

from datetime import date
import time
from typing import Any

import pandas as pd
import requests

from .base import MarketDataError, MarketDataProvider


class EastmoneyProvider(MarketDataProvider):
    """Direct Eastmoney market-data adapter.

    Public endpoints can change or rate-limit, so this provider is isolated and
    can be replaced without touching the scoring layer.
    """

    name = "eastmoney-direct"
    _HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    _LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"

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
    def _market(code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith(("4", "8", "92")):
            return "BJ"
        if code.startswith(("5", "6", "9")):
            return "SH"
        return "SZ"

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

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        self._throttle()
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise MarketDataError(f"Eastmoney request failed: {exc}") from exc

    def stock_list(self) -> pd.DataFrame:
        # Covers Shanghai, Shenzhen/ChiNext, STAR and Beijing A shares.
        params: dict[str, Any] = {
            "pn": "1",
            "pz": "10000",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f12,f14,f13,f2,f3,f5,f6,f8,f15,f16",
        }
        payload = self._get_json(self._LIST_URL, params)
        diff = ((payload.get("data") or {}).get("diff") or [])
        if not diff:
            raise MarketDataError("Eastmoney returned empty A-share universe")
        rows = []
        for item in diff:
            code = str(item.get("f12", "")).zfill(6)
            if not code.isdigit() or len(code) != 6:
                continue
            rows.append(
                {
                    "code": code,
                    "name": str(item.get("f14") or ""),
                    "market": self._market(code),
                    "latest": item.get("f2"),
                    "pct_change": item.get("f3"),
                    "volume": item.get("f5"),
                    "amount": item.get("f6"),
                    "turnover": item.get("f8"),
                    "high": item.get("f15"),
                    "low": item.get("f16"),
                }
            )
        out = pd.DataFrame(rows).drop_duplicates("code").sort_values("code").reset_index(drop=True)
        if out.empty:
            raise MarketDataError("Eastmoney A-share universe could not be parsed")
        for col in ["latest", "pct_change", "volume", "amount", "turnover", "high", "low"]:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        out.attrs["provider"] = self.name
        return out

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
        try:
            payload = self._get_json(self._HISTORY_URL, params)
        except MarketDataError as exc:
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
        return ""
