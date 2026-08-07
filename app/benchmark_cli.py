from __future__ import annotations

import argparse
from datetime import date, timedelta

from data.validator import validate_ohlcv
from features.regime import classify_price_regime
from providers.benchmark import REFERENCE_ASSETS, make_benchmark_provider


def main() -> None:
    parser = argparse.ArgumentParser(description="宽基指数/ETF历史行情与市场状态")
    parser.add_argument("asset", choices=sorted(REFERENCE_ASSETS), nargs="?", default="csi300")
    parser.add_argument("--provider", choices=["auto", "eastmoney", "akshare", "demo"], default="auto")
    parser.add_argument("--days", type=int, default=500)
    parser.add_argument("--interval", choices=["1d", "5m", "15m", "30m", "60m"], default="1d")
    args = parser.parse_args()

    provider = make_benchmark_provider(args.provider)
    end = date.today()
    start = end - timedelta(days=args.days)
    data = validate_ohlcv(provider.history(args.asset, start, end, interval=args.interval))
    print(f"asset={args.asset} kind={data.attrs.get('kind')} provider={data.attrs.get('provider')}")
    print(data.tail(10).to_string(index=False))
    if args.interval == "1d":
        regimes = classify_price_regime(data).dropna(subset=["regime"])
        print("\nRegime counts:\n" + regimes["regime"].value_counts().to_string())
        print("\nLatest regime:\n" + regimes.tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
