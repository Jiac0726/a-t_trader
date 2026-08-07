from __future__ import annotations

import argparse
from datetime import date, timedelta

from app.cli import make_provider
from backtest.t_engine import CostModel, best_single_t_envelope, mean_reversion_backtest, summarize_trades
from data.cached_provider import CachedProvider
from data.validator import validate_ohlcv
from storage.duckdb_store import DuckDBStore


def main() -> None:
    parser = argparse.ArgumentParser(description="A股5分钟做T回测实验室")
    parser.add_argument("code")
    parser.add_argument("--provider", choices=["auto", "eastmoney", "akshare", "demo"], default="auto")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--mode", choices=["positive", "reverse"], default="positive")
    parser.add_argument("--bottom-shares", type=int, default=1000)
    parser.add_argument("--t-ratio", type=float, default=0.5)
    parser.add_argument("--commission", type=float, default=0.0003)
    parser.add_argument("--min-commission", type=float, default=5.0)
    parser.add_argument("--stamp-duty", type=float, default=0.0005)
    parser.add_argument("--other-rate", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--window", type=int, default=6)
    parser.add_argument("--entry-z", type=float, default=1.0)
    parser.add_argument("--db", default="market.duckdb")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    end = date.today()
    raw = make_provider(args.provider)
    provider = raw if args.no_cache else CachedProvider(raw, DuckDBStore(args.db))
    bars = validate_ohlcv(provider.history(args.code, end - timedelta(days=args.days), end, interval="5m", adjust="qfq"))
    costs = CostModel(
        commission_rate=args.commission,
        min_commission=args.min_commission,
        stamp_duty_sell_rate=args.stamp_duty,
        other_rate_per_side=args.other_rate,
        slippage_bps=args.slippage_bps,
    )
    envelope = best_single_t_envelope(bars, args.mode, args.bottom_shares, args.t_ratio, costs)
    baseline = mean_reversion_backtest(
        bars,
        args.mode,
        args.bottom_shares,
        args.t_ratio,
        costs,
        window=args.window,
        entry_z=args.entry_z,
    )
    print("Hindsight envelope (NOT a tradable strategy):", summarize_trades(envelope).to_dict())
    print("Causal rolling-z baseline:", summarize_trades(baseline).to_dict())
    if not baseline.empty:
        print(baseline.tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
