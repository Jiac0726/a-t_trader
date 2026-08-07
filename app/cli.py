from __future__ import annotations

import argparse
from pathlib import Path

from data.cached_provider import CachedProvider
from providers.akshare_provider import AkshareProvider
from providers.chain import ProviderChain
from providers.demo import DemoProvider
from providers.eastmoney import EastmoneyProvider
from scanner.market_scanner import scan_codes, scan_universe
from storage.duckdb_store import DuckDBStore


def read_codes(path: str) -> list[str]:
    return [x.strip() for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]


def make_provider(name: str):
    if name == "demo":
        return DemoProvider()
    if name == "eastmoney":
        return EastmoneyProvider()
    if name == "akshare":
        return AkshareProvider()
    if name == "auto":
        return ProviderChain([EastmoneyProvider(), AkshareProvider()])
    raise ValueError(name)


def main() -> None:
    parser = argparse.ArgumentParser(description="A股做T历史适合度扫描器")
    parser.add_argument("--watchlist", default="config/watchlist.txt")
    parser.add_argument("--provider", choices=["auto", "eastmoney", "akshare", "demo"], default="auto")
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--output", default="output/t_scores.csv")
    parser.add_argument("--db", default="market.duckdb")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--all", action="store_true", help="scan A-share universe instead of watchlist")
    parser.add_argument("--limit", type=int, default=None, help="limit universe scan for first hydration/testing")
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--min-spot-amount", type=float, default=0.0, help="prefilter current amount in CNY when universe endpoint provides it")
    parser.add_argument("--refresh-universe", action="store_true")
    args = parser.parse_args()

    raw_provider = make_provider(args.provider)
    store = DuckDBStore(args.db)
    provider = raw_provider if args.no_cache else CachedProvider(raw_provider, store)

    if args.refresh_universe and isinstance(provider, CachedProvider):
        provider.refresh_stock_list()

    if args.all:
        result = scan_universe(
            provider,
            limit=args.limit,
            exclude_st=not args.include_st,
            min_spot_amount=args.min_spot_amount,
            lookback=args.lookback,
            store=None if args.no_cache else store,
        )
    else:
        codes = read_codes(args.watchlist)
        result = scan_codes(codes, provider, lookback=args.lookback, store=None if args.no_cache else store)

    cols = ["code", "name", "score", "grade", "avg_amplitude", "avg_intraday_space", "median_amount", "max_drawdown", "provider", "error"]
    if result.empty:
        print("No scan results")
    else:
        print(result[cols].to_string(index=False))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
