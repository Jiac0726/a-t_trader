from __future__ import annotations

import os
from datetime import date
from typing import Any

import pandas as pd
import requests

from .base import MarketDataError, MarketDataProvider, NoMarketData


class TushareHistoryProvider(MarketDataProvider):
    """Optional authorized daily-history source using the Tushare Pro HTTP API.

    The adapter has no SDK dependency. It is enabled only when a token is
    supplied explicitly or through ``TUSHARE_TOKEN``. Tushare ``daily`` is raw
    (unadjusted); qfq is constructed from the official ``adj_factor`` series
    and anchored to the latest factor inside the requested window.

    Tushare daily units are converted to the project's normalized schema:
    ``vol`` hands -> shares (x100) and ``amount`` thousand CNY -> CNY (x1000).
    """

    name = "tushare-history"
    _URL = "https://api.tushare.pro"

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ):
        self.token = (token if token is not None else os.getenv("TUSHARE_TOKEN", "")).strip()
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    @property
    def available(self) -> bool:
        return bool(self.token)

    def _require_token(self) -> None:
        if not self.token:
            raise MarketDataError("Tushare provider requires TUSHARE_TOKEN")

    @staticmethod
    def _date(value: date | str) -> str:
        return pd.Timestamp(value).strftime("%Y%m%d")

    @staticmethod
    def _ts_code(code: str) -> str:
        raw = str(code).strip().upper()
        if "." in raw:
            symbol, suffix = raw.split(".", 1)
            suffix = {"SH": "SH", "SSE": "SH", "SZ": "SZ", "SZSE": "SZ", "BJ": "BJ", "BSE": "BJ"}.get(suffix, suffix)
            return f"{symbol.zfill(6)}.{suffix}"
        code = raw.zfill(6)
        if code.startswith(("920", "43", "83", "87")):
            return f"{code}.BJ"
        if code.startswith(("5", "6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    def _query(self, api_name: str, params: dict[str, Any], fields: str) -> pd.DataFrame:
        self._require_token()
        body = {
            "api_name": api_name,
            "token": self.token,
            "params": params,
            "fields": fields,
        }
        try:
            response = self.session.post(self._URL, json=body, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise MarketDataError(f"Tushare {api_name} request failed: {exc}") from exc

        code = payload.get("code")
        if code not in {0, None}:
            raise MarketDataError(f"Tushare {api_name} error {code}: {payload.get('msg', '')}")
        data = payload.get("data") or {}
        columns = data.get("fields") or []
        items = data.get("items") or []
        if not columns or not items:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame(items, columns=columns)

    def stock_list(self) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for exchange, market in (("SSE", "SH"), ("SZSE", "SZ"), ("BSE", "BJ")):
            raw = self._query(
                "stock_basic",
                {"exchange": exchange, "list_status": "L"},
                "ts_code,symbol,name,exchange,list_date,delist_date,list_status",
            )
            if raw.empty:
                continue
            out = pd.DataFrame(
                {
                    "code": raw["symbol"].astype(str).str.zfill(6),
                    "name": raw["name"].astype(str),
                    "market": market,
                    "listing_date": pd.to_datetime(raw.get("list_date"), errors="coerce"),
                    "source": "tushare-stock-basic",
                }
            )
            frames.append(out)
        if not frames:
            raise NoMarketData("Tushare stock_basic returned no A-share securities")
        out = pd.concat(frames, ignore_index=True).drop_duplicates(["market", "code"], keep="last")
        out.attrs["provider"] = self.name
        return out.reset_index(drop=True)

    def history(self, code, start, end, interval="1d", adjust="qfq") -> pd.DataFrame:
        if interval not in {"1d", "day"}:
            raise NoMarketData("Tushare fallback currently supports daily bars only")
        if adjust not in {"qfq", "none", ""}:
            raise NoMarketData(f"Tushare fallback does not expose {adjust} in this audited adapter")

        ts_code = self._ts_code(code)
        start_date, end_date = self._date(start), self._date(end)
        daily = self._query(
            "daily",
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
            "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        )
        if daily.empty:
            raise NoMarketData(f"Tushare returned no daily data for {ts_code}")

        daily["trade_date"] = pd.to_datetime(daily["trade_date"], errors="coerce")
        for column in ["open", "high", "low", "close", "vol", "amount", "pct_chg", "change"]:
            daily[column] = pd.to_numeric(daily[column], errors="coerce")
        daily = daily.dropna(subset=["trade_date", "open", "high", "low", "close"]).copy()
        if daily.empty:
            raise NoMarketData(f"Tushare daily rows were not parseable for {ts_code}")

        if adjust == "qfq":
            factor = self._query(
                "adj_factor",
                {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                "ts_code,trade_date,adj_factor",
            )
            if factor.empty:
                raise NoMarketData(f"Tushare returned no adj_factor for {ts_code}")
            factor["trade_date"] = pd.to_datetime(factor["trade_date"], errors="coerce")
            factor["adj_factor"] = pd.to_numeric(factor["adj_factor"], errors="coerce")
            factor = factor.dropna(subset=["trade_date", "adj_factor"]).sort_values("trade_date")
            if factor.empty or float(factor.iloc[-1]["adj_factor"]) == 0:
                raise NoMarketData(f"Tushare adj_factor invalid for {ts_code}")
            anchor = float(factor.iloc[-1]["adj_factor"])
            daily = daily.merge(factor[["trade_date", "adj_factor"]], on="trade_date", how="left")
            daily["adj_factor"] = daily["adj_factor"].ffill().bfill()
            if daily["adj_factor"].isna().any():
                raise NoMarketData(f"Tushare adj_factor does not cover daily rows for {ts_code}")
            scale = daily["adj_factor"] / anchor
            for column in ["open", "high", "low", "close"]:
                daily[column] = daily[column] * scale

        out = pd.DataFrame(
            {
                "datetime": daily["trade_date"],
                "open": daily["open"],
                "high": daily["high"],
                "low": daily["low"],
                "close": daily["close"],
                "volume": daily["vol"] * 100.0,
                "amount": daily["amount"] * 1000.0,
                "pct_change": daily["pct_chg"],
                "change": daily["change"],
            }
        ).sort_values("datetime").reset_index(drop=True)
        out.attrs.update(
            {
                "provider": self.name,
                "code": str(code).strip().zfill(6),
                "tushare_ts_code": ts_code,
                "adjust": "qfq" if adjust == "qfq" else "none",
                "volume_unit": "shares",
                "amount_unit": "CNY",
            }
        )
        return out
