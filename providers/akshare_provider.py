from __future__ import annotations

from datetime import date
import pandas as pd

from .base import MarketDataError, MarketDataProvider


class AkshareProvider(MarketDataProvider):
    name = "akshare"

    def _ak(self):
        try:
            import akshare as ak
        except ImportError as exc:
            raise MarketDataError("AKShare is not installed. Run: pip install akshare") from exc
        return ak

    @staticmethod
    def _date_str(value: date | str) -> str:
        if isinstance(value, date):
            return value.strftime("%Y%m%d")
        return str(value).replace("-", "")[:8]

    def history(self, code: str, start: date | str, end: date | str, interval: str = "1d", adjust: str = "qfq") -> pd.DataFrame:
        if interval != "1d":
            raise MarketDataError("AKShare fallback currently supports daily history only in this MVP")
        ak = self._ak()
        try:
            raw = ak.stock_zh_a_hist(
                symbol=str(code).zfill(6),
                period="daily",
                start_date=self._date_str(start),
                end_date=self._date_str(end),
                adjust=adjust if adjust in {"qfq", "hfq", ""} else "qfq",
            )
        except Exception as exc:
            raise MarketDataError(f"AKShare request failed for {code}: {exc}") from exc
        if raw is None or raw.empty:
            raise MarketDataError(f"AKShare returned no data for {code}")

        rename = {
            "日期": "datetime",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_change",
            "涨跌额": "change",
            "换手率": "turnover",
        }
        df = raw.rename(columns=rename)
        required = ["datetime", "open", "high", "low", "close", "volume", "amount"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise MarketDataError(f"AKShare schema changed; missing columns: {missing}")
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
        df.attrs["provider"] = self.name
        return df
