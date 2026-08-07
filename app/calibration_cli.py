from __future__ import annotations

import argparse
from datetime import date, timedelta

from app.cli import make_provider
from backtest.t_engine import CostModel
from calibration.walkforward import (
    attach_forward_labels,
    build_opportunity_history,
    build_score_history,
    evaluate_score_walk_forward,
    rank_ic,
    score_bucket_summary,
)
from calibration.optimizer import (
    assess_promotion_gate,
    summarize_model_comparison,
    summarize_weight_stability,
    walk_forward_optimize_weights,
)
from data.cached_provider import CachedProvider
from data.validator import validate_ohlcv
from storage.duckdb_store import DuckDBStore


def main() -> None:
    parser = argparse.ArgumentParser(description="T Score walk-forward / OOS 验证")
    parser.add_argument("code")
    parser.add_argument("--provider", choices=["auto", "eastmoney", "akshare", "demo"], default="auto")
    parser.add_argument("--days", type=int, default=500)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--mode", choices=["positive", "reverse"], default="positive")
    parser.add_argument("--min-train", type=int, default=60)
    parser.add_argument("--test-size", type=int, default=20)
    parser.add_argument("--db", default="market.duckdb")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--optimize", action="store_true", help="仅在训练折内优化6项T Score权重，并在未来折评估")
    parser.add_argument("--candidates", type=int, default=256, help="每个外层训练折搜索的候选权重数量")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-weight", type=float, default=0.55, help="任一单项权重上限")
    parser.add_argument("--random-trials", type=int, default=200, help="每个未来测试折的随机零假设次数")
    args = parser.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    raw = make_provider(args.provider)
    provider = raw if args.no_cache else CachedProvider(raw, DuckDBStore(args.db))

    daily = validate_ohlcv(provider.history(args.code, start, end, interval="1d", adjust="qfq"))
    intraday = validate_ohlcv(provider.history(args.code, start, end, interval="5m", adjust="qfq"))
    scores = build_score_history(
        args.code,
        daily,
        lookback=args.lookback,
        name=daily.attrs.get("name", ""),
        provider=daily.attrs.get("provider", provider.name),
    )
    opportunities = build_opportunity_history(intraday, mode=args.mode, costs=CostModel())
    labeled = attach_forward_labels(scores, opportunities, horizon=args.horizon)
    clean = labeled.dropna(subset=["forward_opportunity_pct"]).reset_index(drop=True)

    print("Rows:", len(clean))
    print("Full-sample rank IC (diagnostic only):", round(rank_ic(clean), 4) if len(clean) else "n/a")
    print("\nScore buckets:")
    print(score_bucket_summary(clean).to_string(index=False))

    metrics, _ = evaluate_score_walk_forward(
        clean,
        min_train_size=args.min_train,
        test_size=args.test_size,
        gap=args.horizon,
    )
    print("\nWalk-forward OOS folds:")
    print(metrics.to_string(index=False) if not metrics.empty else "Not enough labeled rows for requested split sizes")

    if args.optimize:
        opt_metrics, opt_weights, _ = walk_forward_optimize_weights(
            clean,
            min_train_size=args.min_train,
            test_size=args.test_size,
            gap=args.horizon,
            n_candidates=args.candidates,
            seed=args.seed,
            max_weight=args.max_weight,
            random_trials=args.random_trials,
        )
        print("\nTraining-fold-only optimized weights:")
        print(opt_weights.to_string(index=False) if not opt_weights.empty else "Not enough rows")
        print("\nOptimized vs baselines (outer OOS folds):")
        comparison = summarize_model_comparison(opt_metrics)
        print(comparison.to_string(index=False) if not comparison.empty else "Not enough rows")
        print("\nWeight stability across outer folds:")
        stability = summarize_weight_stability(opt_weights)
        print(stability.to_string(index=False) if not stability.empty else "Not enough rows")
        print("\nPromotion gate (must pass before replacing v0.1):")
        print(assess_promotion_gate(opt_metrics))
        print("\nOuter fold detail:")
        print(opt_metrics.to_string(index=False) if not opt_metrics.empty else "Not enough rows")


if __name__ == "__main__":
    main()
