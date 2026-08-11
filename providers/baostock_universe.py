from __future__ import annotations

from datetime import date

import pandas as pd

from .base import MarketDataError, MarketDataProvider
from .baostock_master import BaostockSecurityMasterProvider
from .official_universe import OfficialExchangeUniverseProvider
from .tencent_universe import TencentDiscoveredUniverseProvider


class BaostockSnapshotUniverseProvider(MarketDataProvider):
    """Hosted-friendly current A-share identity provider.

    Preferred path: SH/SZ identity from BaoStock's latest point-in-time snapshot
    plus BSE best-effort identity.  Some hosted egress IPs are rejected by
    BaoStock.  In that case we fail over inside this provider to Tencent batch
    quote discovery, so callers do not have to wait for every stock-list
    endpoint in the outer ProviderChain to fail.
    """

    name = "baostock-snapshot-universe"

    def __init__(
        self,
        security_master: BaostockSecurityMasterProvider | None = None,
        as_of=None,
        bse_provider: OfficialExchangeUniverseProvider | None = None,
        include_bse: bool = True,
        fallback_provider: MarketDataProvider | None = None,
    ):
        self.security_master = security_master or BaostockSecurityMasterProvider()
        self.as_of = as_of
        self.bse_provider = bse_provider or OfficialExchangeUniverseProvider(timeout=5.0)
        self.include_bse = bool(include_bse)
        self.fallback_provider = fallback_provider or TencentDiscoveredUniverseProvider()

    def _fallback(self, exc: Exception) -> pd.DataFrame:
        out = self.fallback_provider.stock_list().copy()
        if out is None or out.empty:
            raise MarketDataError(f"BaoStock failed ({exc}); fallback universe is empty") from exc
        out.attrs["provider"] = getattr(self.fallback_provider, "name", "fallback-universe")
        out.attrs["fallback_from"] = self.name
        out.attrs["fallback_reason"] = str(exc)
        markets = set(out["market"].astype(str)) if "market" in out.columns else set()
        out.attrs["market_coverage"] = sorted(markets)
        out.attrs["missing_markets"] = sorted({"SH", "SZ", "BJ"} - markets)
        return out

    def stock_list(self) -> pd.DataFrame:
        requested = pd.Timestamp(self.as_of or date.today()).normalize()
        try:
            snapshot = self.security_master.snapshot(requested)
            if snapshot is None or snapshot.empty:
                raise MarketDataError("BaoStock security snapshot is empty")

            shsz = snapshot.copy().rename(columns={"exchange": "market"})
            shsz = shsz[shsz["market"].astype(str).isin(["SH", "SZ"])].copy()
            if "trade_status" in shsz.columns:
                shsz["trade_status"] = pd.to_numeric(shsz["trade_status"], errors="coerce").fillna(0).astype(int)
            shsz["code"] = shsz["code"].astype(str).str.zfill(6)
            shsz["name"] = shsz.get("name", "").astype(str)
            shsz["listing_date"] = pd.NaT
            shsz["source"] = "baostock-point-in-time"
            keep = [c for c in ["code", "name", "market", "listing_date", "source", "trade_status"] if c in shsz.columns]
            shsz = shsz[keep].drop_duplicates(["market", "code"], keep="last").reset_index(drop=True)

            if set(shsz["market"].astype(str)) != {"SH", "SZ"}:
                raise MarketDataError("BaoStock snapshot does not contain both SH and SZ stocks")
            if len(shsz) < 3000:
                raise MarketDataError(f"BaoStock SH/SZ universe suspiciously small: {len(shsz)}")
        except Exception as exc:
            return self._fallback(exc)

        parts = [shsz]
        bse_error = ""
        if self.include_bse:
            try:
                bse = self.bse_provider.bse_stock_list().copy()
                if not bse.empty:
                    if "trade_status" not in bse.columns:
                        bse["trade_status"] = 1
                    parts.append(bse[[c for c in keep if c in bse.columns]])
            except Exception as exc:
                bse_error = str(exc)

        out = pd.concat(parts, ignore_index=True, sort=False)
        out["code"] = out["code"].astype(str).str.zfill(6)
        out = out.drop_duplicates(["market", "code"], keep="last").reset_index(drop=True)
        markets = set(out["market"].astype(str))

        out.attrs["provider"] = self.name
        out.attrs["snapshot_as_of"] = snapshot.attrs.get("resolved_trade_date", str(requested.date()))
        out.attrs["market_coverage"] = sorted(markets)
        out.attrs["missing_markets"] = sorted({"SH", "SZ", "BJ"} - markets)
        if bse_error:
            out.attrs["bse_warning"] = bse_error
        return out

    def history(self, code, start, end, interval="1d", adjust="qfq") -> pd.DataFrame:
        raise MarketDataError("BaoStock snapshot universe provider supplies identity only")
