from __future__ import annotations

from datetime import date
import pandas as pd

from providers.base import MarketDataProvider
from providers.demo import DemoProvider
from providers.retrying import RetryingProvider


class FlakyProvider(MarketDataProvider):
    name = "flaky"

    def __init__(self):
        self.calls = 0
        self.demo = DemoProvider()

    def history(self, code: str, start: date | str, end: date | str, interval: str = "1d", adjust: str = "qfq") -> pd.DataFrame:
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("temporary")
        return self.demo.history(code, start, end, interval=interval, adjust=adjust)


def test_retrying_provider_recovers_transient_failure():
    raw = FlakyProvider()
    provider = RetryingProvider(raw, attempts=3, base_delay=0, jitter=0, sleep_fn=lambda _: None)
    df = provider.history("300059", date(2026, 5, 1), date(2026, 8, 7))
    assert not df.empty
    assert raw.calls == 3
