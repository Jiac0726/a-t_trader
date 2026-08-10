from __future__ import annotations

from datetime import date

import pandas as pd

from .base import MarketDataError, MarketDataProvider, NoMarketData
from storage.duckdb_store import DuckDBStore


class LocalDuckDBProvider(MarketDataProvider):
    """Read-only market-data provider backed entirely by the local DuckDB file.

    Normal scans should use this provider so external data sources are never on
    the critical path. Network providers are reserved for the separate database
    maintenance workflow (initial load, daily increment, targeted repair).
    """

    name = "local-duckdb"

    def __init__(self, store: DuckDBStore):
        self.store = store

    def stock_list(self) -> pd.DataFrame:
        stocks = self.store.load_stock_list()
        if stocks is None or stocks.empty:
            raise MarketDataError(
                "本地股票池为空。请先进入“数据管理”执行“更新股票池/初始化数据库”。"
            )
        out = stocks.copy()
        out["code"] = out["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        if "market" not in out.columns:
            out["market"] = out["code"].map(self._infer_market)
        if "name" not in out.columns:
            out["name"] = ""
        out.attrs["provider"] = self.name
        return out

    @staticmethod
    def _infer_market(code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith(("92", "8", "4")):
            return "BJ"
        if code.startswith(("5", "6")):
            return "SH"
        return "SZ"

    def history(
        self,
        code: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        code = str(code).strip().zfill(6)
        adjust = str(adjust or "none")
        out = self.store.load_history(code, interval, start, end, adjust=adjust)
        if out is None or out.empty:
            raise NoMarketData(
                f"本地数据库缺少 {code} {interval}/{adjust} 历史数据。"
                "请到“数据管理”执行增量更新或指定股票修复。"
            )

        name = ""
        stocks = self.store.load_stock_list()
        if stocks is not None and not stocks.empty and {"code", "name"}.issubset(stocks.columns):
            codes = stocks["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
            match = stocks.loc[codes.eq(code)]
            if not match.empty:
                name = str(match.iloc[0].get("name", ""))

        out.attrs["name"] = name
        out.attrs["provider"] = self.name
        out.attrs["adjust"] = adjust
        return out

    def stock_name(self, code: str) -> str:
        code = str(code).strip().zfill(6)
        stocks = self.store.load_stock_list()
        if stocks is None or stocks.empty or "code" not in stocks.columns:
            return ""
        codes = stocks["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        match = stocks.loc[codes.eq(code)]
        return "" if match.empty else str(match.iloc[0].get("name", ""))
