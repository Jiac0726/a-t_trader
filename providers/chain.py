from __future__ import annotations

from datetime import date
import pandas as pd

from .base import MarketDataError, MarketDataProvider


class ProviderChain(MarketDataProvider):
    name = "provider-chain"

    def __init__(self, providers: list[MarketDataProvider]):
        if not providers:
            raise ValueError("ProviderChain requires at least one provider")
        self.providers = providers

    def history(self, code: str, start: date | str, end: date | str, interval: str = "1d", adjust: str = "qfq") -> pd.DataFrame:
        errors: list[str] = []
        for provider in self.providers:
            try:
                df = provider.history(code, start, end, interval=interval, adjust=adjust)
                df.attrs["provider"] = provider.name
                return df
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
        raise MarketDataError("All providers failed: " + " | ".join(errors))

    def stock_list(self) -> pd.DataFrame:
        errors: list[str] = []
        for provider in self.providers:
            try:
                df = provider.stock_list()
                if df is None or df.empty:
                    raise MarketDataError("empty stock list")
                df.attrs["provider"] = provider.name
                return df
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
        raise MarketDataError("All stock-list providers failed: " + " | ".join(errors))
