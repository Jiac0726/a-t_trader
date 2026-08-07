from __future__ import annotations

import argparse
from datetime import date, timedelta

import pandas as pd

from app.cli import make_provider
from backtest.t_engine import CostModel
from calibration.cross_sectional import (
    assess_cross_sectional_promotion_gate,
    combine_labeled_panels,
    cross_sectional_quantile_summary,
    summarize_cross_sectional,
    walk_forward_cross_sectional_optimize,
)
from calibration.walkforward import attach_forward_labels, build_opportunity_history, build_score_history
from calibration.regime_analysis import regime_coverage, summarize_cross_sectional_by_regime
from data.cached_provider import CachedProvider
from data.validator import validate_ohlcv
from features.regime import attach_market_regime, classify_price_regime
from storage.duckdb_store import DuckDBStore
from storage.security_snapshot_store import DuckDBSecuritySnapshotStore
from providers.baostock_master import BaostockSecurityMasterProvider
from providers.security_master import filter_panel_by_lifecycle, filter_panel_by_snapshots
from providers.benchmark import BENCHMARKS, AkshareBenchmarkProvider, BenchmarkProviderChain, EastmoneyBenchmarkProvider
from data.cached_security_master import CachedSecurityMasterProvider


def _parse_codes(value: str) -> list[str]:
    return [x.strip().zfill(6) for x in value.replace(";", ",").split(",") if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="多股票横截面 T Score OOS 校准")
    parser.add_argument("--codes", default="300059,601899,601138,000063,300750,300308")
    parser.add_argument("--provider", choices=["auto", "eastmoney", "akshare", "demo"], default="demo")
    parser.add_argument("--days", type=int, default=500)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--mode", choices=["positive", "reverse"], default="positive")
    parser.add_argument("--min-assets", type=int, default=5)
    parser.add_argument("--min-train-dates", type=int, default=60)
    parser.add_argument("--test-dates", type=int, default=20)
    parser.add_argument("--candidates", type=int, default=128)
    parser.add_argument("--random-trials", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--db", default="market.duckdb")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--regime-benchmark", choices=[""] + sorted(BENCHMARKS), default="", help="preferred explicit broad-market benchmark for causal regime labels")
    parser.add_argument("--regime-code", default="", help="legacy stock/proxy code for regime labels; prefer --regime-benchmark")
    parser.add_argument("--security-master", choices=["none", "baostock-lifecycle", "baostock-snapshot"], default="none", help="optional point-in-time universe filter before cross-sectional calibration")
    args = parser.parse_args()

    raw = make_provider(args.provider)
    provider = raw if args.no_cache else CachedProvider(raw, DuckDBStore(args.db))
    end = date.today()
    start = end - timedelta(days=args.days)
    frames: dict[str, pd.DataFrame] = {}
    for code in _parse_codes(args.codes):
        try:
            daily = validate_ohlcv(provider.history(code, start, end, interval="1d", adjust="qfq"))
            intraday = validate_ohlcv(provider.history(code, start, end, interval="5m", adjust="qfq"))
            scores = build_score_history(code, daily, lookback=args.lookback, name=daily.attrs.get("name", ""), provider=daily.attrs.get("provider", provider.name))
            opp = build_opportunity_history(intraday, mode=args.mode, costs=CostModel())
            frames[code] = attach_forward_labels(scores, opp, horizon=args.horizon)
            print(f"loaded {code}: {len(frames[code])} score dates")
        except Exception as exc:
            print(f"skip {code}: {exc}")

    panel = combine_labeled_panels(frames).dropna(subset=["forward_opportunity_pct"]).reset_index(drop=True)
    if args.security_master == "baostock-lifecycle":
        master = BaostockSecurityMasterProvider().master()
        before = len(panel)
        panel = filter_panel_by_lifecycle(panel, master, unknown="drop")
        print(f"lifecycle membership filter: {before} -> {len(panel)} rows")
    elif args.security_master == "baostock-snapshot":
        raw_master = BaostockSecurityMasterProvider()
        master_provider = raw_master if args.no_cache else CachedSecurityMasterProvider(raw_master, DuckDBSecuritySnapshotStore(args.db))
        dates = sorted(pd.to_datetime(panel["date"]).dt.normalize().unique())
        snapshots = master_provider.snapshot_many(dates)
        before = len(panel)
        panel = filter_panel_by_snapshots(panel, snapshots, include_suspended=False, unknown_dates="drop")
        print(f"exact historical snapshot filter (tradable only): {before} -> {len(panel)} rows; snapshots={snapshots['as_of'].nunique() if not snapshots.empty else 0}")

    regime_source = ""
    regime_daily = None
    if args.regime_benchmark:
        if args.provider == "eastmoney":
            benchmark_provider = EastmoneyBenchmarkProvider()
        elif args.provider == "akshare":
            benchmark_provider = AkshareBenchmarkProvider()
        else:
            benchmark_provider = BenchmarkProviderChain()
        regime_daily = validate_ohlcv(benchmark_provider.history(args.regime_benchmark, start, end, interval="1d"))
        regime_source = f"benchmark:{args.regime_benchmark}"
    elif args.regime_code:
        regime_daily = validate_ohlcv(provider.history(args.regime_code, start, end, interval="1d", adjust="qfq"))
        regime_source = f"legacy-code:{args.regime_code}"

    if regime_daily is not None:
        regime_history = classify_price_regime(regime_daily)
        before = len(panel)
        panel = attach_market_regime(panel, regime_history, unknown="drop")
        print(f"market regime filter/attach: {before} -> {len(panel)} rows using {regime_source}")
        print("\nRegime coverage:")
        print(regime_coverage(panel).to_string(index=False))
        print("\nFixed-score cross-sectional diagnostics by market regime:")
        regime_diag = summarize_cross_sectional_by_regime(
            panel,
            score_cols=("score", "amplitude_score", "liquidity_score", "tradable_space_score"),
            min_assets=args.min_assets,
        )
        print(regime_diag.to_string(index=False) if not regime_diag.empty else "Not enough assets/dates by regime")

    if panel.empty:
        raise SystemExit("No labeled multi-stock panel")
    print("\nCross-sectional fixed v0.1 summary:")
    print(summarize_cross_sectional(panel, min_assets=args.min_assets).to_dict())
    print("\nCross-sectional score quantiles:")
    print(cross_sectional_quantile_summary(panel, min_assets=args.min_assets).to_string(index=False))
    metrics, weights = walk_forward_cross_sectional_optimize(
        panel,
        min_train_dates=args.min_train_dates,
        test_dates=args.test_dates,
        gap_dates=args.horizon,
        min_assets=args.min_assets,
        n_candidates=args.candidates,
        random_trials=args.random_trials,
        seed=args.seed,
    )
    print("\nOuter OOS cross-sectional folds:")
    print(metrics.to_string(index=False) if not metrics.empty else "Not enough dates")
    print("\nTraining-fold weights:")
    print(weights.to_string(index=False) if not weights.empty else "Not enough dates")
    print("\nCross-sectional promotion gate:")
    print(assess_cross_sectional_promotion_gate(metrics))
    print("\nWarning: real historical calibration must use a point-in-time universe; using only today's surviving stocks can create survivorship bias.")


if __name__ == "__main__":
    main()
