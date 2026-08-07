from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import time

from .base import MarketDataProvider


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    ok: bool
    latency_ms: float
    universe_rows: int = 0
    history_rows: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def check_provider_health(
    provider: MarketDataProvider,
    sample_code: str = "300059",
    history_days: int = 45,
    check_universe: bool = True,
) -> ProviderHealth:
    started = time.perf_counter()
    try:
        universe_rows = 0
        if check_universe:
            universe = provider.stock_list()
            universe_rows = len(universe)
        end = date.today()
        history = provider.history(sample_code, end - timedelta(days=history_days), end, interval="1d", adjust="qfq")
        return ProviderHealth(
            provider=provider.name,
            ok=not history.empty,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            universe_rows=universe_rows,
            history_rows=len(history),
        )
    except Exception as exc:
        return ProviderHealth(
            provider=provider.name,
            ok=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            error=str(exc),
        )
