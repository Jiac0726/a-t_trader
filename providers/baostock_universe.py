from __future__ import annotations

from datetime import date

import pandas as pd

from .base import MarketDataError, MarketDataProvider
from .baostock_master import BaostockSecurityMasterProvider


class BaostockSnapshotUniverseProvider(MarketDataProvider):
    """Current identity fallback from the latest available BaoStock snapshot.

    BaoStock is consistently reachable from hosted runners for SH/SZ identity.
    BJ coverage is best-effort and callers must inspect the returned market set
    instead of assuming all three exchanges are present.
    """

    name = "baostock-snapshot-universe"

    def __init__(self, security_master: BaostockSecurityMasterProvider | None = None, as_of=None):
        self.security_master = security_master or BaostockSecurityMasterProvider()
        self.as_of = as_of

    def stock_list(self) -> pd.DataFrame:
        requested = pd.Timestamp(self.as_of or date.today()).normalize()
        snapshot = self.security_master.snapshot(requested)
        if snapshot is None or snapshot.empty:
            raise MarketDataError("BaoStock security snapshot is empty")
        out = snapshot.copy().rename(columns={"exchange": "market"})
        if "trade_status" in out.columns:
            out["trade_status"] = pd.to_numeric(out["trade_status"], errors="coerce").fillna(0).astype(int)
        out["code"] = out["code"].astype(str).str.zfill(6)
        out["name"] = out.get("name", "").astype(str)
        out["listing_date"] = pd.NaT
        out["source"] = self.name
        keep = [c for c in ["code", "name", "market", "listing_date", "source", "trade_status"] if c in out.columns]
        out = out[keep].drop_duplicates(["market", "code"], keep="last").reset_index(drop=True)
        markets = set(out["market"].astype(str)) if "market" in out.columns else set()
        if not {"SH", "SZ"}.issubset(markets):
            raise MarketDataError(f"BaoStock snapshot missing SH/SZ markets: {sorted(markets)}")
        if len(out) < 3000:
            raise MarketDataError(f"BaoStock snapshot universe suspiciously small: {len(out)}")
        out.attrs["provider"] = self.name
        out.attrs["snapshot_as_of"] = snapshot.attrs.get("resolved_trade_date", str(requested.date()))
        out.attrs["market_coverage"] = sorted(markets)
        return out

    def history(self, code, start, end, interval="1d", adjust="qfq") -> pd.DataFrame:
        raise MarketDataError("BaoStock snapshot universe provider supplies identity only")
