from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import requests

from .base import MarketDataError, MarketDataProvider, NoMarketData


class TencentHistoryProvider(MarketDataProvider):
    """Independent Tencent K-line fallback, including BSE code namespaces.

    Current BSE 920 codes and legacy BSE 43/83/87 codes are routed explicitly
    to the `bj` namespace. Legacy routing exists for historical continuity
    probes only; old codes are never used to build the current stock universe.

    Tencent's K-line payload exposes OHLCV but not a trustworthy historical
    turnover amount field in the same schema. We therefore keep a required
    `amount` column as an explicitly estimated value (volume in lots × 100 ×
    OHLC mean) and mark the dataframe with `amount_estimated=True`. Liquidity
    calibration should prefer a provider with reported historical amount.
    """

    name = "tencent-history"
    _DAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _MIN_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"

    def __init__(self, timeout: float = 12.0, session: requests.Session | None = None):
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
                "Referer": "https://gu.qq.com/",
            }
        )

    @staticmethod
    def _symbol(code: str) -> str:
        raw = str(code).strip().lower()
        if raw.startswith(("sh", "sz", "bj")):
            return raw
        code = raw.zfill(6)
        # BSE must be tested before generic routing. The 43/83/87 namespaces
        # are legacy listed-company codes retained only for historical probes.
        if code.startswith(("920", "43", "83", "87")):
            return f"bj{code}"
        if code.startswith(("5", "6", "9")):
            return f"sh{code}"
        return f"sz{code}"

    @staticmethod
    def _date(value: date | str) -> str:
        return pd.Timestamp(value).strftime("%Y-%m-%d")

    @staticmethod
    def _estimate_amount(df: pd.DataFrame) -> pd.Series:
        typical = df[["open", "high", "low", "close"]].mean(axis=1)
        # Tencent volume is quoted in lots for these CN K-line endpoints.
        return pd.to_numeric(df["volume"], errors="coerce") * 100.0 * typical

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise MarketDataError(f"Tencent K-line request failed: {exc}") from exc

    def _daily(self, symbol: str, start, end, adjust: str) -> pd.DataFrame:
        mode = adjust if adjust in {"qfq", "hfq"} else ""
        params = {
            "param": f"{symbol},day,{self._date(start)},{self._date(end)},2000,{mode or 'qfq'}",
        }
        payload = self._get_json(self._DAY_URL, params)
        stock = (payload.get("data") or {}).get(symbol) or {}
        preferred = f"{mode}day" if mode else "day"
        rows = stock.get(preferred) or stock.get("day") or stock.get("qfqday") or stock.get("hfqday") or []
        if not rows:
            raise NoMarketData(f"Tencent returned no daily rows for {symbol}")
        parsed = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            parsed.append(
                {
                    "datetime": row[0],
                    "open": row[1],
                    "close": row[2],
                    "high": row[3],
                    "low": row[4],
                    "volume": row[5],
                }
            )
        return pd.DataFrame(parsed)

    def _minute(self, symbol: str, interval: str) -> pd.DataFrame:
        period = str(interval).lower().replace("m", "")
        if period not in {"1", "5", "15", "30", "60"}:
            raise NoMarketData(f"Tencent minute fallback does not support {interval}")
        key = f"m{period}"
        params = {"param": f"{symbol},{key},,320"}
        payload = self._get_json(self._MIN_URL, params)
        stock = (payload.get("data") or {}).get(symbol) or {}
        rows = stock.get(key) or []
        if not rows:
            raise NoMarketData(f"Tencent returned no {interval} rows for {symbol}")
        parsed = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            parsed.append(
                {
                    "datetime": row[0],
                    "open": row[1],
                    "close": row[2],
                    "high": row[3],
                    "low": row[4],
                    "volume": row[5],
                }
            )
        return pd.DataFrame(parsed)

    def history(self, code, start, end, interval="1d", adjust="qfq") -> pd.DataFrame:
        symbol = self._symbol(code)
        raw = self._daily(symbol, start, end, adjust) if interval in {"1d", "day"} else self._minute(symbol, interval)
        if raw.empty:
            raise NoMarketData(f"Tencent returned no parseable rows for {symbol}")
        raw["datetime"] = pd.to_datetime(raw["datetime"], errors="coerce")
        for col in ["open", "high", "low", "close", "volume"]:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
        raw = raw.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
        if raw.empty:
            raise NoMarketData(f"Tencent rows were not parseable for {symbol}")
        raw["amount"] = self._estimate_amount(raw)
        raw.attrs.update(
            {
                "provider": self.name,
                "code": str(code).strip().lower().removeprefix("sh").removeprefix("sz").removeprefix("bj").zfill(6),
                "tencent_symbol": symbol,
                "amount_estimated": True,
                "amount_quality": "estimated_from_volume_lots_x100_x_ohlc_mean",
            }
        )
        return raw

    def stock_list(self) -> pd.DataFrame:
        raise MarketDataError("Tencent history provider intentionally does not implement stock_list")
