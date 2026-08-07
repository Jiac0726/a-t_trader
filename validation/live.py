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

    def to_dict(self):
        return asdict(self)


@dataclass(slots=True)
class ValidationReport:
    checks: list[CheckResult]

    @property
    def failed(self):
        return [x for x in self.checks if x.status == "FAIL"]

    @property
    def warnings(self):
        return [x for x in self.checks if x.status == "WARN"]

    @property
    def ok(self):
        return not self.failed

    def to_dict(self):
        return {
            "ok": self.ok,
            "failed": len(self.failed),
            "warnings": len(self.warnings),
            "checks": [x.to_dict() for x in self.checks],
        }


def _check(name, fn, *, warning=False):
    try:
        detail, data = fn()
        return CheckResult(name, "PASS", detail, data or {})
    except Exception as exc:
        return CheckResult(name, "WARN" if warning else "FAIL", str(exc), {})


def _normalized_market_keys(df: pd.DataFrame, market_col: str, code_col: str = "code") -> set[str]:
    if df is None or df.empty or code_col not in df.columns or market_col not in df.columns:
        return set()
    x = df[[market_col, code_col]].dropna().copy()
    x[market_col] = x[market_col].astype(str).str.upper().str.strip()
    x[code_col] = x[code_col].astype(str).str.zfill(6)
    return set((x[market_col] + ":" + x[code_col]).tolist())


def run_live_validation(
    market_provider,
    benchmark_provider,
    *,
    benchmark="csi300",
    reference_etf="csi300_etf_sh",
    representative_codes=("600519", "000001", "300750"),
    security_master_provider=None,
    persistence_probe=None,
    universe_floor=3000,
    require_markets=("SH", "SZ", "BJ"),
    membership_overlap_floor=0.90,
    end: date | None = None,
) -> ValidationReport:
    """Run internet-connected merge-gate checks without mutating research state.

    The function is dependency-injected so all control flow remains unit-testable
    offline. Real endpoint coverage is only claimed when this runner is executed
    on a connected machine and the generated JSON report is preserved.
    """
    end = end or date.today()
    start_daily = end - timedelta(days=45)
    start_intraday = end - timedelta(days=7)
    checks: list[CheckResult] = []
    universe_holder: dict[str, pd.DataFrame] = {}

    def universe_check():
        df = market_provider.stock_list()
        if df is None or df.empty:
            raise RuntimeError("empty market universe")
        if len(df) < universe_floor:
            raise RuntimeError(f"universe too small: {len(df)} < {universe_floor}")
        if not {"code", "name", "market"}.issubset(df.columns):
            raise RuntimeError("universe missing code/name/market")
        counts = {str(k).upper(): int(v) for k, v in df["market"].value_counts().to_dict().items()}
        missing = [market for market in require_markets if counts.get(market, 0) <= 0]
        if missing:
            raise RuntimeError(f"missing required markets {missing}: {counts}")
        universe_holder["df"] = df.copy()
        return f"{len(df)} securities; markets={counts}", {"rows": len(df), "markets": counts}

    checks.append(_check("market_universe", universe_check))

    codes = list(dict.fromkeys(str(x).zfill(6) for x in representative_codes))
    universe = universe_holder.get("df")
    if universe is not None and not universe.empty:
        for market in require_markets:
            subset = universe[universe["market"].astype(str).str.upper() == market]
            if not subset.empty:
                code = str(subset.iloc[0]["code"]).zfill(6)
                if code not in codes:
                    codes.append(code)

    for code in codes:
        def daily_check(code=code):
            df = validate_ohlcv(market_provider.history(code, start_daily, end, interval="1d", adjust="qfq"))
            if len(df) < 5:
                raise RuntimeError(f"too few daily rows for {code}: {len(df)}")
            return (
                f"{code}: {len(df)} daily rows through {df['datetime'].max()}",
                {"code": code, "rows": len(df), "provider": df.attrs.get("provider", "")},
            )

        checks.append(_check(f"daily_{code}", daily_check))

    first = codes[0]

    def intraday_check():
        df = validate_ohlcv(market_provider.history(first, start_intraday, end, interval="5m", adjust="qfq"))
        if len(df) < 10:
            raise RuntimeError(f"too few 5m rows for {first}: {len(df)}")
        return f"{first}: {len(df)} 5m rows", {"code": first, "rows": len(df), "provider": df.attrs.get("provider", "")}

    checks.append(_check("intraday_5m", intraday_check))

    def benchmark_check():
        df = validate_ohlcv(benchmark_provider.history(benchmark, start_daily, end, interval="1d"))
        if len(df) < 5:
            raise RuntimeError(f"too few benchmark rows: {len(df)}")
        if df.attrs.get("kind") not in {None, "index"}:
            raise RuntimeError(f"benchmark kind mismatch: {df.attrs.get('kind')}")
        return (
            f"{benchmark}: {len(df)} daily rows",
            {"benchmark": benchmark, "rows": len(df), "provider": df.attrs.get("provider", ""), "secid": df.attrs.get("eastmoney_secid", "")},
        )

    checks.append(_check("benchmark_history", benchmark_check))

    if reference_etf:
        def etf_check():
            df = validate_ohlcv(benchmark_provider.history(reference_etf, start_daily, end, interval="1d"))
            if len(df) < 5:
                raise RuntimeError(f"too few ETF rows: {len(df)}")
            if df.attrs.get("kind") not in {None, "etf"}:
                raise RuntimeError(f"ETF kind mismatch: {df.attrs.get('kind')}")
            return (
                f"{reference_etf}: {len(df)} daily rows",
                {"asset": reference_etf, "rows": len(df), "provider": df.attrs.get("provider", ""), "secid": df.attrs.get("eastmoney_secid", "")},
            )

        checks.append(_check("reference_etf_history", etf_check))

    snapshot_holder: dict[str, pd.DataFrame] = {}
    if security_master_provider is not None:
        def snapshot_check():
            snap = security_master_provider.snapshot(end)
            if snap is None or snap.empty:
                raise RuntimeError("empty point-in-time security snapshot")
            if len(snap) < 2500:
                raise RuntimeError(f"security snapshot suspiciously small: {len(snap)}")
            required = {"code", "name", "as_of"}
            if not required.issubset(snap.columns):
                raise RuntimeError(f"snapshot missing {required - set(snap.columns)}")
            snapshot_holder["df"] = snap.copy()
            return (
                f"snapshot rows={len(snap)} as_of={snap['as_of'].iloc[0]}",
                {"rows": len(snap), "as_of": str(snap["as_of"].iloc[0]), "provider": getattr(security_master_provider, "name", "")},
            )

        checks.append(_check("point_in_time_snapshot", snapshot_check))

        def shsz_overlap_check():
            market_df = universe_holder.get("df")
            snap = snapshot_holder.get("df")
            if market_df is None or snap is None:
                raise RuntimeError("universe/snapshot prerequisite failed")
            if "exchange" not in snap.columns:
                raise RuntimeError("snapshot missing exchange column for cross-source membership check")
            market_keys = _normalized_market_keys(market_df[market_df["market"].isin(["SH", "SZ"])], "market")
            snapshot_keys = _normalized_market_keys(snap[snap["exchange"].isin(["SH", "SZ"])], "exchange")
            if not market_keys:
                raise RuntimeError("empty SH/SZ market set")
            overlap = len(market_keys & snapshot_keys) / len(market_keys)
            if overlap < float(membership_overlap_floor):
                raise RuntimeError(f"SH/SZ point-in-time overlap too low: {overlap:.2%} < {membership_overlap_floor:.2%}")
            return (
                f"SH/SZ membership overlap={overlap:.2%}",
                {"market_rows": len(market_keys), "snapshot_rows": len(snapshot_keys), "intersection": len(market_keys & snapshot_keys), "overlap": overlap},
            )

        checks.append(_check("point_in_time_overlap_shsz", shsz_overlap_check))

        def bj_overlap_check():
            market_df = universe_holder.get("df")
            snap = snapshot_holder.get("df")
            if market_df is None or snap is None or "exchange" not in snap.columns:
                raise RuntimeError("universe/snapshot prerequisite failed")
            market_keys = _normalized_market_keys(market_df[market_df["market"] == "BJ"], "market")
            snapshot_keys = _normalized_market_keys(snap[snap["exchange"] == "BJ"], "exchange")
            if not market_keys:
                raise RuntimeError("market provider has no BJ rows")
            overlap = len(market_keys & snapshot_keys) / len(market_keys)
            if not snapshot_keys:
                raise RuntimeError("BaoStock snapshot has no BJ rows; BSE coverage remains unverified")
            return (
                f"BJ membership overlap={overlap:.2%} (informational until independently verified)",
                {"market_rows": len(market_keys), "snapshot_rows": len(snapshot_keys), "intersection": len(market_keys & snapshot_keys), "overlap": overlap},
            )

        checks.append(_check("point_in_time_overlap_bj", bj_overlap_check, warning=True))

    if persistence_probe is not None:
        checks.append(_check("duckdb_roundtrip", persistence_probe))

    return ValidationReport(checks)
