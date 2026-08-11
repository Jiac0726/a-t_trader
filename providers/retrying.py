from __future__ import annotations

from datetime import date
import random
import time
from typing import Callable

import pandas as pd

from .base import MarketDataProvider, NoMarketData


class RetryingProvider(MarketDataProvider):
    """Small retry/backoff wrapper for transient upstream failures."""

    def __init__(
        self,
        provider: MarketDataProvider,
        attempts: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 4.0,
        jitter: float = 0.15,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.provider = provider
        self.name = provider.name
        self.attempts = max(1, int(attempts))
        self.base_delay = max(0.0, float(base_delay))
        self.max_delay = max(self.base_delay, float(max_delay))
        self.jitter = max(0.0, float(jitter))
        self.sleep_fn = sleep_fn

    def _call(self, func, *args, **kwargs):
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                return func(*args, **kwargs)
            except NoMarketData:
                raise
            except Exception as exc:  # providers normalize most upstream errors already
                last_error = exc
                if attempt >= self.attempts:
                    raise
                delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
                if self.jitter:
                    delay += random.uniform(0.0, self.jitter)
                self.sleep_fn(delay)
        assert last_error is not None
        raise last_error

    def history(
        self,
        code: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        return self._call(self.provider.history, code, start, end, interval=interval, adjust=adjust)

    def stock_list(self) -> pd.DataFrame:
        return self._call(self.provider.stock_list)

    def stock_name(self, code: str) -> str:
        return self.provider.stock_name(code)
