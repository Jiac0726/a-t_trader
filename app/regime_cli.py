from __future__ import annotations

import argparse
from datetime import date, timedelta

from app.cli import make_provider
from data.cached_provider import CachedProvider
from data.validator import validate_ohlcv
from features.regime import classify_price_regime
from providers.benchmark import REFERENCE_ASSETS, make_benchmark_provider
from storage.duckdb_store import DuckDBStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Causal trailing market/price regime classifier")
    parser.add_argument("code", nargs="?", default="", help="legacy stock/proxy code; prefer --benchmark")
    parser.add_argument("--benchmark", choices=[""] + sorted(REFERENCE_ASSETS), default="csi300")
    parser.add_argument("--provider", choices=["auto", "eastmoney", "akshare", "demo"], default="auto")
    parser.add_argument("--days", type=int, default=500)
    parser.add_argument("--db", default="market.duckdb")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    if args.benchmark:
        reference_provider = make_benchmark_provider(args.provider)
        daily = validate_ohlcv(reference_provider.history(args.benchmark, start, end, interval="1d"))
        source = f"reference:{args.benchmark}"
    else:
        if not args.code:
            raise SystemExit("provide --benchmark or a legacy code")
        raw = make_provider(args.provider)
        provider = raw if args.no_cache else CachedProvider(raw, DuckDBStore(args.db))
        daily = validate_ohlcv(provider.history(args.code, start, end, interval="1d", adjust="qfq"))
        source = f"code:{args.code}"
    regimes = classify_price_regime(daily)
    clean = regimes.dropna(subset=["regime"])
    print(f"Source: {source}")
    print("Regime counts:")
    print(clean["regime"].value_counts().to_string())
    print("\nLatest regime rows:")
    print(clean.tail(30).to_string(index=False))


if __name__ == "__main__":
    main()
