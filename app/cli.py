from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from data.cached_provider import CachedProvider
from data.hydrator import hydrate_codes
from providers.akshare_provider import AkshareProvider
from providers.baostock_daily import BaostockHistoryProvider
from providers.baostock_master import BaostockSecurityMasterProvider
from providers.baostock_universe import BaostockSnapshotUniverseProvider
from providers.chain import ProviderChain
from providers.composite_universe import BaostockBseUniverseProvider
from providers.demo import DemoProvider
from providers.eastmoney import EastmoneyProvider
from providers.health import check_provider_health
from providers.official_universe import OfficialExchangeUniverseProvider
from providers.retrying import RetryingProvider
from providers.tencent_history import TencentHistoryProvider
from providers.tushare_history import TushareHistoryProvider
from scanner.market_scanner import scan_codes, select_universe
from storage.duckdb_store import DuckDBStore


def read_codes(path: str) -> list[str]:
    return [x.strip() for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]


def _optional_tushare():
    provider = TushareHistoryProvider()
    return provider if provider.available else None


def make_raw_provider(name: str):
    if name == "demo":
        return DemoProvider()
    if name == "eastmoney":
        return EastmoneyProvider()
    if name == "akshare":
        return AkshareProvider()
    if name == "tushare":
        return TushareHistoryProvider()
    if name == "auto":
        providers = [
            BaostockHistoryProvider(),
            EastmoneyProvider(),
            AkshareProvider(),
            TencentHistoryProvider(),
        ]
        tushare = _optional_tushare()
        if tushare is not None:
            providers.append(tushare)
        return ProviderChain(providers)
    raise ValueError(name)


def make_provider(name: str, retries: int = 2):
    """Price-history provider chain."""
    if name == "demo":
        return DemoProvider()
    attempts = max(1, retries + 1)
    if name == "eastmoney":
        return RetryingProvider(EastmoneyProvider(), attempts=attempts)
    if name == "akshare":
        return RetryingProvider(AkshareProvider(), attempts=attempts)
    if name == "tushare":
        return RetryingProvider(TushareHistoryProvider(), attempts=attempts)
    if name == "auto":
        providers = [
            RetryingProvider(BaostockHistoryProvider(), attempts=attempts),
            RetryingProvider(EastmoneyProvider(), attempts=attempts),
            RetryingProvider(AkshareProvider(), attempts=attempts),
            RetryingProvider(TencentHistoryProvider(), attempts=attempts),
        ]
        tushare = _optional_tushare()
        if tushare is not None:
            providers.append(RetryingProvider(tushare, attempts=attempts))
        return ProviderChain(providers)
    raise ValueError(name)


def make_universe_provider(name: str = "auto", retries: int = 2):
    """Security-identity chain independent from price-history providers.

    Hosted environments frequently block quote/exchange HTTP endpoints while
    BaoStock remains reachable. Try BaoStock SH/SZ + BSE first, then preserve a
    BaoStock SH/SZ-only fallback so the app can report partial market coverage
    instead of failing the entire scan.
    """
    if name == "demo":
        return DemoProvider()
    attempts = max(1, retries + 1)
    if name == "eastmoney":
        return RetryingProvider(EastmoneyProvider(), attempts=attempts)
    if name == "akshare":
        return RetryingProvider(AkshareProvider(), attempts=attempts)
    if name == "tushare":
        return RetryingProvider(TushareHistoryProvider(), attempts=attempts)
    if name != "auto":
        raise ValueError(name)

    master = BaostockSecurityMasterProvider()
    providers = [
        RetryingProvider(BaostockBseUniverseProvider(master), attempts=attempts),
        RetryingProvider(BaostockSnapshotUniverseProvider(master), attempts=attempts),
    ]
    tushare = _optional_tushare()
    if tushare is not None:
        providers.append(RetryingProvider(tushare, attempts=attempts))
    providers.extend(
        [
            RetryingProvider(EastmoneyProvider(), attempts=attempts),
            RetryingProvider(AkshareProvider(), attempts=attempts),
            RetryingProvider(OfficialExchangeUniverseProvider(), attempts=attempts),
        ]
    )
    return ProviderChain(providers)


def main() -> None:
    parser = argparse.ArgumentParser(description="A股做T历史适合度扫描器")
    parser.add_argument("--watchlist", default="config/watchlist.txt")
    parser.add_argument("--provider", choices=["auto", "eastmoney", "akshare", "tushare", "demo"], default="auto")
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--calendar-days", type=int, default=140)
    parser.add_argument("--output", default="output/t_scores.csv")
    parser.add_argument("--db", default="market.duckdb")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--all", action="store_true", help="scan A-share universe instead of watchlist")
    parser.add_argument("--limit", type=int, default=None, help="limit universe scan for first hydration/testing")
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--min-spot-amount", type=float, default=0.0, help="prefilter current amount in CNY when universe endpoint provides it")
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--retries", type=int, default=2, help="transient retry count for normal provider calls")
    parser.add_argument("--hydrate-workers", type=int, default=4, help="bounded parallel workers for first daily-K cache hydration")
    parser.add_argument("--hydrate-rps", type=float, default=4.0, help="aggregate request rate during parallel hydration")
    parser.add_argument("--no-prehydrate", action="store_true", help="disable concurrent pre-hydration before all-market scan")
    parser.add_argument("--health", action="store_true", help="run provider health check and exit")
    args = parser.parse_args()

    if args.health:
        report = check_provider_health(make_provider(args.provider, retries=args.retries))
        print(report.to_dict())
        return

    raw_provider = make_provider(args.provider, retries=args.retries)
    store = DuckDBStore(args.db)
    provider = raw_provider if args.no_cache else CachedProvider(raw_provider, store)

    end = date.today()
    start = end - timedelta(days=args.calendar_days)

    if args.all:
        universe_provider = make_universe_provider(args.provider, retries=args.retries)
        candidates = select_universe(
            universe_provider,
            limit=args.limit,
            exclude_st=not args.include_st,
            min_spot_amount=args.min_spot_amount,
        )
        codes = candidates["code"].tolist() if not candidates.empty else []
        if codes and not args.no_cache and not args.no_prehydrate and args.hydrate_workers > 1:
            report = hydrate_codes(
                codes,
                provider_factory=lambda: make_raw_provider(args.provider),
                store=store,
                start=start,
                end=end,
                workers=args.hydrate_workers,
                requests_per_second=args.hydrate_rps,
                retries=args.retries,
            )
            print(
                "Hydration:",
                {
                    "codes": report.codes,
                    "requested_ranges": report.requested_ranges,
                    "succeeded_ranges": report.succeeded_ranges,
                    "failed_ranges": report.failed_ranges,
                    "fetched_rows": report.fetched_rows,
                    "elapsed_seconds": report.elapsed_seconds,
                },
            )
            if report.failures:
                print("Hydration failures (first 10):")
                for failure in report.failures[:10]:
                    print(f"  {failure.code} {failure.start}..{failure.end}: {failure.error}")
        result = scan_codes(
            codes,
            provider,
            end=end,
            calendar_days=args.calendar_days,
            lookback=args.lookback,
            store=None if args.no_cache else store,
        )
    else:
        codes = read_codes(args.watchlist)
        result = scan_codes(
            codes,
            provider,
            end=end,
            calendar_days=args.calendar_days,
            lookback=args.lookback,
            store=None if args.no_cache else store,
        )

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
