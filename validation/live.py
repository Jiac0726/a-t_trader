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
    daily_required_markets=None,
    daily_warning_markets=(),
    membership_overlap_floor=0.90,
    market_candidate_limit=12,
    end: date | None = None,
) -> ValidationReport:
    """Run internet-connected merge-gate checks without mutating research state.

    ``require_markets`` controls identity coverage in the current security
    universe. Daily-price coverage is separated deliberately: by default it is
    equally strict, but callers may set ``daily_required_markets`` and
    ``daily_warning_markets`` independently. This lets CI keep BSE identity hard
    while reporting unavailable long-window BSE public history as a visible WARN
    when no authorized/local history source is configured.

    Explicit representative codes are always hard checks. Required market
    probes may search a bounded candidate set. Warning-only probes are diagnostic
    and intentionally try only one representative to avoid turning a known
    unsupported public-history path into a long CI timeout. For BSE, native
    ``920002`` is preferred when it is present in the current universe.
    """
    end = end or date.today()
    start_daily = end - timedelta(days=45)
    start_intraday = end - timedelta(days=7)
    checks: list[CheckResult] = []
    universe_holder: dict[str, pd.DataFrame] = {}
    required_daily = tuple(require_markets if daily_required_markets is None else daily_required_markets)
    warning_daily = tuple(m for m in daily_warning_markets if m not in required_daily)

    def universe_check():
        df = market_provider.stock_list()
        if df is None or df.empty:
            raise RuntimeError("empty market universe")
        if len(df) < universe_floor:
            raise RuntimeError(f"universe too small: {len(df)} < {universe_floor}")
        if not {"code", "name", "market"}.issubset(df.columns):
            raise RuntimeError("universe missing code/name/market")
        df = df.copy()
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["market"] = df["market"].astype(str).str.upper().str.strip()
        counts = {str(k).upper(): int(v) for k, v in df["market"].value_counts().to_dict().items()}
        missing = [market for market in require_markets if counts.get(market, 0) <= 0]
        if missing:
            raise RuntimeError(f"missing required markets {missing}: {counts}")
        universe_holder["df"] = df
        return f"{len(df)} securities; markets={counts}", {"rows": len(df), "markets": counts}

    checks.append(_check("market_universe", universe_check))

    explicit_codes = list(dict.fromkeys(str(x).zfill(6) for x in representative_codes))

    def load_daily(code: str):
        df = market_provider.history(code, start_daily, end, interval="1d", adjust="qfq")
        clean = validate_ohlcv(df)
        if len(clean) < 5:
            raise RuntimeError(f"too few daily rows for {code}: {len(clean)}")
        return clean, df.attrs.get("provider", clean.attrs.get("provider", ""))

    for code in explicit_codes:
        def daily_check(code=code):
            df, provider_name = load_daily(code)
            return (
                f"{code}: {len(df)} daily rows through {df['datetime'].max()}",
                {"code": code, "rows": len(df), "provider": provider_name, "selection": "explicit"},
            )

        checks.append(_check(f"daily_{code}", daily_check))

    universe = universe_holder.get("df")
    covered_markets: set[str] = set()
    if universe is not None and not universe.empty:
        code_to_market = universe.drop_duplicates("code").set_index("code")["market"].to_dict()
        covered_markets = {str(code_to_market.get(code, "")).upper() for code in explicit_codes}
        covered_markets.discard("")

        def append_market_daily_probe(market: str, *, warning: bool) -> None:
            if market in covered_markets:
                return

            def market_daily_check(market=market, warning=warning):
                subset = universe[universe["market"] == market]
                if subset.empty:
                    raise RuntimeError(f"no {market} candidates in current universe")
                candidates = subset["code"].astype(str).str.zfill(6).tolist()
                if market == "BJ" and "920002" in candidates:
                    candidates = ["920002", *[code for code in candidates if code != "920002"]]
                probe_limit = 1 if warning else max(1, int(market_candidate_limit))
                attempts: list[str] = []
                for code in candidates[:probe_limit]:
                    if code in explicit_codes:
                        continue
                    try:
                        df, provider_name = load_daily(code)
                        return (
                            f"{market} representative {code}: {len(df)} daily rows through {df['datetime'].max()}",
                            {
                                "market": market,
                                "code": code,
                                "rows": len(df),
                                "provider": provider_name,
                                "selection": "preferred_or_first_usable_current_universe_candidate",
                                "attempted_before_success": attempts,
                                "warning_only_probe": warning,
                            },
                        )
                    except Exception as exc:
                        attempts.append(f"{code}: {exc}")
                sample = attempts[:5]
                raise RuntimeError(
                    f"no usable {market} daily representative among {probe_limit} candidate(s); "
                    f"sample_errors={sample}"
                )

            checks.append(_check(f"daily_market_{market}", market_daily_check, warning=warning))

        for market in required_daily:
            append_market_daily_probe(str(market).upper(), warning=False)
        for market in warning_daily:
            append_market_daily_probe(str(market).upper(), warning=True)

    first = explicit_codes[0]

    def intraday_check():
        # T-cost validation must use the historical nominal price level.
        df = validate_ohlcv(market_provider.history(first, start_intraday, end, interval="5m", adjust="none"))
        if len(df) < 10:
            raise RuntimeError(f"too few 5m rows for {first}: {len(df)}")
        return f"{first}: {len(df)} 5m rows", {"code": first, "rows": len(df), "provider": df.attrs.get("provider", ""), "adjust": "none"}

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
