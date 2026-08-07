from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any
import pandas as pd
from data.validator import validate_ohlcv


@dataclass(slots=True)
class CheckResult:
    name: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)
    def to_dict(self): return asdict(self)


@dataclass(slots=True)
class ValidationReport:
    checks: list[CheckResult]
    @property
    def failed(self): return [x for x in self.checks if x.status == "FAIL"]
    @property
    def warnings(self): return [x for x in self.checks if x.status == "WARN"]
    @property
    def ok(self): return not self.failed
    def to_dict(self):
        return {"ok": self.ok, "failed": len(self.failed), "warnings": len(self.warnings), "checks": [x.to_dict() for x in self.checks]}


def _check(name, fn, *, warning=False):
    try:
        detail, data = fn()
        return CheckResult(name, "PASS", detail, data or {})
    except Exception as exc:
        return CheckResult(name, "WARN" if warning else "FAIL", str(exc), {})


def run_live_validation(
    market_provider,
    benchmark_provider,
    *,
    benchmark="csi300",
    representative_codes=("600519", "000001", "300750"),
    security_master_provider=None,
    persistence_probe=None,
    universe_floor=3000,
    end: date | None = None,
) -> ValidationReport:
    end = end or date.today()
    start_daily = end - timedelta(days=45)
    start_intraday = end - timedelta(days=7)
    checks: list[CheckResult] = []

    def universe_check():
        df = market_provider.stock_list()
        if df is None or df.empty: raise RuntimeError("empty market universe")
        if len(df) < universe_floor: raise RuntimeError(f"universe too small: {len(df)} < {universe_floor}")
        if not {"code","name","market"}.issubset(df.columns): raise RuntimeError("universe missing code/name/market")
        counts = df["market"].value_counts().to_dict()
        if counts.get("SH",0) == 0 or counts.get("SZ",0) == 0: raise RuntimeError(f"missing SH/SZ rows: {counts}")
        return f"{len(df)} securities; markets={counts}", {"rows":len(df),"markets":counts}
    checks.append(_check("market_universe", universe_check))

    for code in representative_codes:
        def daily_check(code=code):
            df = validate_ohlcv(market_provider.history(code, start_daily, end, interval="1d", adjust="qfq"))
            if len(df) < 5: raise RuntimeError(f"too few daily rows for {code}: {len(df)}")
            return f"{code}: {len(df)} daily rows through {df['datetime'].max()}", {"code":code,"rows":len(df)}
        checks.append(_check(f"daily_{code}", daily_check))

    first = representative_codes[0]
    def intraday_check():
        df = validate_ohlcv(market_provider.history(first, start_intraday, end, interval="5m", adjust="qfq"))
        if len(df) < 10: raise RuntimeError(f"too few 5m rows for {first}: {len(df)}")
        return f"{first}: {len(df)} 5m rows", {"code":first,"rows":len(df)}
    checks.append(_check("intraday_5m", intraday_check))

    def benchmark_check():
        df = validate_ohlcv(benchmark_provider.history(benchmark, start_daily, end, interval="1d"))
        if len(df) < 5: raise RuntimeError(f"too few benchmark rows: {len(df)}")
        return f"{benchmark}: {len(df)} daily rows", {"benchmark":benchmark,"rows":len(df),"provider":df.attrs.get("provider","")}
    checks.append(_check("benchmark_history", benchmark_check))

    if security_master_provider is not None:
        def snapshot_check():
            snap = security_master_provider.snapshot(end)
            if snap is None or snap.empty: raise RuntimeError("empty point-in-time security snapshot")
            if len(snap) < 2500: raise RuntimeError(f"security snapshot suspiciously small: {len(snap)}")
            required={"code","name","as_of"}
            if not required.issubset(snap.columns): raise RuntimeError(f"snapshot missing {required-set(snap.columns)}")
            return f"snapshot rows={len(snap)} as_of={snap['as_of'].iloc[0]}", {"rows":len(snap),"as_of":str(snap['as_of'].iloc[0])}
        checks.append(_check("point_in_time_snapshot", snapshot_check, warning=True))

    if persistence_probe is not None:
        checks.append(_check("duckdb_roundtrip", persistence_probe))

    return ValidationReport(checks)
