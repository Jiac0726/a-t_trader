from __future__ import annotations

import argparse
from datetime import date, timedelta

from app.cli import make_provider
from data.cached_provider import CachedProvider
from data.validator import validate_ohlcv
from features.regime import classify_price_regime
from storage.duckdb_store import DuckDBStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Causal trailing market/price regime classifier")
    parser.add_argument("code", help="benchmark or stock code supported by the chosen provider")
    parser.add_argument("--provider", choices=["auto", "eastmoney", "akshare", "demo"], default="demo")
    parser.add_argument("--days", type=int, default=500)
    parser.add_argument("--db", default="market.duckdb")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    raw = make_provider(args.provider)
    provider = raw if args.no_cache else CachedProvider(raw, DuckDBStore(args.db))
    end = date.today()
    start = end - timedelta(days=args.days)
    daily = validate_ohlcv(provider.history(args.code, start, end, interval="1d", adjust="qfq"))
    regimes = classify_price_regime(daily)
    clean = regimes.dropna(subset=["regime"])
    print("Regime counts:")
    print(clean["regime"].value_counts().to_string())
    print("\nLatest regime rows:")
    print(clean.tail(30).to_string(index=False))


if __name__ == "__main__":
    main()
