from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
import pandas as pd


class MarketDataError(RuntimeError):
    pass


class NoMarketData(MarketDataError):
    """The request was valid but the requested range contained no market rows."""


class MarketDataProvider(ABC):
    name = "base"

    @abstractmethod
    def history(
        self,
        code: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """Return normalized OHLCV data.

        Required columns: datetime, open, high, low, close, volume, amount.
        Optional: amplitude, pct_change, turnover.
        """
        raise NotImplementedError

    def stock_list(self) -> pd.DataFrame:
        """Return normalized A-share universe with at least code/name/market.

        Providers that cannot resolve the market universe should raise
        MarketDataError so ProviderChain can fall back to another source.
        """
        raise MarketDataError(f"{self.name} does not implement stock_list")

    def stock_name(self, code: str) -> str:
        return ""
