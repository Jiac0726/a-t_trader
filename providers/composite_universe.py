from __future__ import annotations

from datetime import date

import pandas as pd

from .base import MarketDataError, MarketDataProvider
from .official_universe import OfficialExchangeUniverseProvider


class BaostockBseUniverseProvider(MarketDataProvider):
    """Current full A-share identity from proven BaoStock SH/SZ + official BSE.

    GitHub-hosted runners can reach BaoStock while some SSE/SZSE/quote hosts may
    be network-blocked. This provider avoids making all three markets share one
    network failure domain: SH/SZ identities come from a point-in-time BaoStock
    snapshot, while BJ identities are fetched only from the BSE official list.
    """

    name = "baostock-bse-universe"

    def __init__(self, security_master, bse_provider: OfficialExchangeUniverseProvider | None = None, as_of=None):
        self.security_master = security_master
        self.bse_provider = bse_provider or OfficialExchangeUniverseProvider()
        self.as_of = as_of

    def stock_list(self) -> pd.DataFrame:
        requested = pd.Timestamp(self.as_of or date.today()).normalize()
        snapshot = self.security_master.snapshot(requested)
        if snapshot is None or snapshot.empty:
            raise MarketDataError("BaoStock current SH/SZ snapshot is empty")
        shsz = snapshot[snapshot["exchange"].isin(["SH", "SZ"])].copy()
        if shsz.empty:
            raise MarketDataError("BaoStock current snapshot contains no SH/SZ stocks")
        shsz = shsz.rename(columns={"exchange": "market"})
        shsz["listing_date"] = pd.NaT
        shsz["source"] = "baostock-point-in-time"
        shsz = shsz[["code", "name", "market", "listing_date", "source"]]

        bse = self.bse_provider.bse_stock_list().copy()
        if bse.empty:
            raise MarketDataError("BSE official current stock list is empty")
        out = pd.concat([shsz, bse], ignore_index=True)
        out["code"] = out["code"].astype(str).str.zfill(6)
        out = out.drop_duplicates(["market", "code"], keep="last")
        markets = set(out["market"])
        if markets != {"SH", "SZ", "BJ"}:
            raise MarketDataError(f"Composite universe missing markets: {sorted({'SH','SZ','BJ'} - markets)}")
        if len(out) < 3000:
            raise MarketDataError(f"Composite A-share universe suspiciously small: {len(out)}")
        # Representative/live smoke tests should prefer the current 920 BSE
        # namespace instead of legacy migrated codes that third-party quote
        # services may keep as stale aliases.
        out["_priority"] = ((out["market"] == "BJ") & out["code"].str.startswith("92")).astype(int)
        out = out.sort_values(["market", "_priority", "code"], ascending=[True, False, True]).drop(columns="_priority").reset_index(drop=True)
        out.attrs["provider"] = self.name
        out.attrs["snapshot_as_of"] = snapshot.attrs.get("resolved_trade_date", str(requested.date()))
        return out

    def history(self, code, start, end, interval="1d", adjust="qfq") -> pd.DataFrame:
        raise MarketDataError("Composite universe provider supplies identity only")
