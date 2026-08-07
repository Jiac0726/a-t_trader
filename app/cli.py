from __future__ import annotations

import argparse
from pathlib import Path

from providers.akshare_provider import AkshareProvider
from providers.chain import ProviderChain
from providers.demo import DemoProvider
from providers.eastmoney import EastmoneyProvider
from scanner.market_scanner import scan_codes


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
    args = parser.parse_args()

    codes = read_codes(args.watchlist)
    result = scan_codes(codes, make_provider(args.provider), lookback=args.lookback)
    print(result[["code", "name", "score", "grade", "avg_amplitude", "avg_intraday_space", "median_amount", "max_drawdown", "provider", "error"]].to_string(index=False))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
