from __future__ import annotations

import argparse
from datetime import date

from providers.baostock_master import BaostockSecurityMasterProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Point-in-time A-share security universe")
    parser.add_argument("--as-of", default=str(date.today()), help="calendar date YYYY-MM-DD; resolves to latest trading day at/before it")
    parser.add_argument("--include-suspended", action="store_true", help="keep tradeStatus=0 names")
    parser.add_argument("--exchange", choices=["ALL", "SH", "SZ", "BJ"], default="ALL")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    provider = BaostockSecurityMasterProvider()
    universe = provider.snapshot(args.as_of)
    if not args.include_suspended:
        universe = universe[universe["trade_status"] == 1]
    if args.exchange != "ALL":
        universe = universe[universe["exchange"] == args.exchange]
    print("requested:", args.as_of)
    print("resolved trading day:", universe.attrs.get("resolved_trade_date", "n/a"))
    print("stocks:", len(universe))
    print(universe.head(max(1, args.limit)).to_string(index=False))
    print("\nCoverage note:", universe.attrs.get("coverage_note", ""))


if __name__ == "__main__":
    main()
