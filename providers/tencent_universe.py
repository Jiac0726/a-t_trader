from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from .base import MarketDataError, MarketDataProvider
from .tencent_spot import TencentSpotProvider


class TencentDiscoveredUniverseProvider(MarketDataProvider):
    """Discover the current A-share stock universe via Tencent batch quotes.

    This is a hosted-deployment fallback for environments whose egress IPs are
    blocked by dedicated stock-list endpoints.  We only probe code ranges that
    are reserved for A-share stocks, so ETFs, indices and funds are excluded by
    construction.

    Current-code coverage:
    - SH main board: 600/601/603/605
    - STAR Market: 688/689
    - SZ main board: 000/001/002/003
    - ChiNext: 300/301
    - BSE current unified codes: 920
    """

    name = "tencent-discovered-universe"

    STOCK_PREFIXES = (
        "600", "601", "603", "605", "688", "689",
        "000", "001", "002", "003", "300", "301",
        "920",
    )

    def __init__(self, *, spot_provider: TencentSpotProvider | None = None, batch_size: int = 150):
        self.spot_provider = spot_provider or TencentSpotProvider(batch_size=batch_size)
        self._cache: pd.DataFrame | None = None

    @classmethod
    def candidate_codes(cls) -> Iterable[str]:
        for prefix in cls.STOCK_PREFIXES:
            for suffix in range(1000):
                yield f"{prefix}{suffix:03d}"

    def stock_list(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache.copy()

        quotes = self.spot_provider.quotes(self.candidate_codes())
        if quotes is None or quotes.empty:
            raise MarketDataError("Tencent code-range discovery returned no A-share rows")

        required = {"code", "name", "market"}
        missing = required - set(quotes.columns)
        if missing:
            raise MarketDataError(f"Tencent universe rows missing columns: {sorted(missing)}")

        out = quotes.copy()
        out["code"] = out["code"].astype(str).str.zfill(6)
        out["name"] = out["name"].astype(str).str.strip()
        out["market"] = out["market"].astype(str).str.upper()
        out = out[
            out["name"].ne("")
            & out["market"].isin({"SH", "SZ", "BJ"})
            & out["code"].str[:3].isin(self.STOCK_PREFIXES)
        ].copy()
        if out.empty:
            raise MarketDataError("Tencent discovery response contained no normalized A-share stocks")

        # Keep quote fields as a useful bonus for callers, but identity only
        # requires code/name/market.  Deduplicate defensively.
        out = out.sort_values(["market", "code"]).drop_duplicates(["market", "code"], keep="last").reset_index(drop=True)
        out.attrs["provider"] = self.name
        out.attrs["discovery_requested"] = self.spot_provider.last_report.requested
        out.attrs["discovery_returned"] = self.spot_provider.last_report.returned
        out.attrs["discovery_failed_batches"] = self.spot_provider.last_report.failed_batches
        self._cache = out.copy()
        return out

    def history(
        self,
        code: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        raise MarketDataError("Tencent discovered universe supplies security identity only")
