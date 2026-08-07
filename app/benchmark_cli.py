from __future__ import annotations
import argparse
from datetime import date, timedelta
from data.validator import validate_ohlcv
from features.regime import classify_price_regime
from providers.benchmark import BENCHMARKS, AkshareBenchmarkProvider, BenchmarkProviderChain, EastmoneyBenchmarkProvider


def main():
    p = argparse.ArgumentParser(description="宽基指数历史行情与市场状态")
    p.add_argument("benchmark", choices=sorted(BENCHMARKS), nargs="?", default="csi300")
    p.add_argument("--provider", choices=["auto","eastmoney","akshare"], default="auto")
    p.add_argument("--days", type=int, default=500)
    args = p.parse_args()
    provider = {"eastmoney": EastmoneyBenchmarkProvider(), "akshare": AkshareBenchmarkProvider()}.get(args.provider, BenchmarkProviderChain())
    end = date.today(); start = end - timedelta(days=args.days)
    daily = validate_ohlcv(provider.history(args.benchmark, start, end, interval="1d"))
    print(daily.tail(10).to_string(index=False))
    regimes = classify_price_regime(daily).dropna(subset=["regime"])
    print("\nRegime counts:\n" + regimes["regime"].value_counts().to_string())
    print("\nLatest regime:\n" + regimes.tail(10).to_string(index=False))

if __name__ == "__main__":
    main()
