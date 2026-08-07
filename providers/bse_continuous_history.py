from __future__ import annotations

from datetime import timedelta

import pandas as pd

from .base import MarketDataError, MarketDataProvider, NoMarketData
from .bse_code_mapping import BseCodeMappingProvider


class BseContinuousHistoryProvider(MarketDataProvider):
    """Stitch BSE daily history across the 2025 legacy->920 code switch.

    This wrapper is intentionally conservative:
    - only codes present in the official BSE mapping are split;
    - pre-switch data is requested with the exact official legacy code;
    - post-switch data is requested with the current 920 code;
    - no price scaling or synthetic bars are introduced;
    - minute continuity is not claimed here.

    The wrapped history provider must itself know how to route both current and
    legacy BSE identifiers. This class is unit-tested but should only be put on
    a production fallback chain after a connected validation proves the chosen
    upstream can actually return legacy-code bars.
    """

    name = "bse-continuous-history"

    def __init__(self, history_provider: MarketDataProvider, mapping_provider: BseCodeMappingProvider | None = None):
        self.history_provider = history_provider
        self.mapping_provider = mapping_provider or BseCodeMappingProvider()

    def stock_list(self) -> pd.DataFrame:
        raise MarketDataError("BSE continuous history provider intentionally does not implement stock_list")

    @staticmethod
    def _date(value):
        return pd.Timestamp(value).normalize()

    def history(self, code, start, end, interval="1d", adjust="qfq") -> pd.DataFrame:
        current = str(code).strip().lower().removeprefix("bj").zfill(6)
        start_ts = self._date(start)
        end_ts = self._date(end)
        if start_ts > end_ts:
            raise ValueError("start must be <= end")

        if interval not in {"1d", "day"}:
            # No claim of pre/post-switch intraday continuity. Current-code
            # minute history may still be queried through the wrapped provider.
            return self.history_provider.history(current, start, end, interval=interval, adjust=adjust)

        old_code = self.mapping_provider.old_code_for(current) if current.startswith("920") else None
        switch = self.mapping_provider.switch_date.normalize()
        if not old_code:
            out = self.history_provider.history(current, start, end, interval="1d", adjust=adjust)
            out.attrs.setdefault("bse_code_stitched", False)
            return out

        parts: list[pd.DataFrame] = []
        source_segments: list[dict[str, str]] = []

        if start_ts < switch:
            old_end = min(end_ts, switch - timedelta(days=1))
            if old_end >= start_ts:
                old = self.history_provider.history(old_code, start_ts, old_end, interval="1d", adjust=adjust)
                if old is not None and not old.empty:
                    old = old.copy()
                    old["source_code"] = old_code
                    parts.append(old)
                    source_segments.append({"code": old_code, "start": str(start_ts.date()), "end": str(old_end.date())})

        if end_ts >= switch:
            new_start = max(start_ts, switch)
            if end_ts >= new_start:
                new = self.history_provider.history(current, new_start, end_ts, interval="1d", adjust=adjust)
                if new is not None and not new.empty:
                    new = new.copy()
                    new["source_code"] = current
                    parts.append(new)
                    source_segments.append({"code": current, "start": str(new_start.date()), "end": str(end_ts.date())})

        if not parts:
            raise NoMarketData(f"No BSE history returned for mapped pair {old_code}->{current}")

        out = pd.concat(parts, ignore_index=True)
        if "datetime" not in out.columns:
            raise MarketDataError("BSE stitched history missing datetime column")
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
        out = out.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)
        if out.empty:
            raise NoMarketData(f"BSE stitched history became empty for {old_code}->{current}")

        out.attrs.update(
            {
                "provider": self.name,
                "wrapped_provider": getattr(self.history_provider, "name", type(self.history_provider).__name__),
                "bse_code_stitched": len(parts) > 1,
                "current_code": current,
                "legacy_code": old_code,
                "switch_date": str(switch.date()),
                "source_segments": source_segments,
            }
        )
        return out
