from __future__ import annotations

import pandas as pd

from providers.security_master import SecurityMasterProvider, SNAPSHOT_COLUMNS


class CachedSecurityMasterProvider(SecurityMasterProvider):
    """Cache exact historical universe snapshots behind a security-master source."""

    def __init__(self, provider: SecurityMasterProvider, store):
        self.provider = provider
        self.store = store

    @property
    def name(self) -> str:
        return f"cached:{self.provider.name}"

    def snapshot(self, as_of) -> pd.DataFrame:
        day = pd.Timestamp(as_of).normalize()
        cached = self.store.load_security_snapshot(day, self.provider.name)
        if cached is not None and not cached.empty:
            return cached
        fresh = self.provider.snapshot(day)
        self.store.save_security_snapshot(fresh, self.provider.name)
        return fresh

    def snapshot_many(self, as_of_dates) -> pd.DataFrame:
        dates = sorted({pd.Timestamp(x).normalize() for x in as_of_dates})
        parts: list[pd.DataFrame] = []
        missing: list[pd.Timestamp] = []
        for day in dates:
            cached = self.store.load_security_snapshot(day, self.provider.name)
            if cached is None or cached.empty:
                missing.append(day)
            else:
                parts.append(cached)
        if missing:
            fresh = self.provider.snapshot_many(missing)
            if fresh is not None and not fresh.empty:
                for day, group in fresh.groupby(pd.to_datetime(fresh["as_of"]).dt.normalize()):
                    group = group.copy()
                    group["as_of"] = pd.Timestamp(day)
                    self.store.save_security_snapshot(group, self.provider.name)
                    parts.append(group)
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=SNAPSHOT_COLUMNS)

    def master(self) -> pd.DataFrame:
        return self.provider.master()
