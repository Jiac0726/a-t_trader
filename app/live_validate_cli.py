from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import pandas as pd

from app.cli import make_provider
from data.cached_security_master import CachedSecurityMasterProvider
from providers.baostock_master import BaostockSecurityMasterProvider
from providers.baostock_daily import BaostockHistoryProvider
from providers.composite_universe import BaostockBseUniverseProvider
from providers.chain import ProviderChain
from providers.retrying import RetryingProvider
from providers.benchmark import ETFS, BENCHMARKS, BaostockBenchmarkProvider, BenchmarkProviderChain, make_benchmark_provider
from storage.duckdb_store import DuckDBStore
from storage.security_snapshot_store import DuckDBSecuritySnapshotStore
from validation.live import run_live_validation


def duckdb_probe(path: str):
    """Use a temporary sibling DB; never pollute the user's research database."""
    base = Path(path).expanduser().resolve()

    def probe():
        base.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="a_t_trader_validate_", suffix=".duckdb", dir=base.parent)
        Path(temp_name).unlink(missing_ok=True)
        try:
            store = DuckDBStore(temp_name)
            sample = pd.DataFrame(
                {
                    "datetime": [pd.Timestamp("2026-01-02")],
                    "open": [1.0],
                    "high": [1.1],
                    "low": [0.9],
                    "close": [1.0],
                    "volume": [100.0],
                    "amount": [1000.0],
                }
            )
            store.save_history("999999", "1d", sample)
            out = store.load_history("999999", "1d")
            if len(out) != 1:
                raise RuntimeError(f"DuckDB history roundtrip rows={len(out)}")

            snap_store = DuckDBSecuritySnapshotStore(temp_name)
            snapshot = pd.DataFrame(
                {
                    "as_of": [pd.Timestamp("2026-01-02")],
                    "code": ["600519"],
                    "exchange": ["SH"],
                    "name": ["probe"],
                    "trade_status": [1],
                    "source_code": ["sh.600519"],
                    "source": ["probe"],
                }
            )
            snap_store.save_security_snapshot(snapshot, "probe")
            snap_out = snap_store.load_security_snapshot("2026-01-02", "probe")
            if len(snap_out) != 1:
                raise RuntimeError(f"DuckDB snapshot roundtrip rows={len(snap_out)}")
            return "history + security-snapshot write/read roundtrip OK", {"history_rows": len(out), "snapshot_rows": len(snap_out)}
        finally:
            try:
                import os
                os.close(fd)
            except OSError:
                pass
            Path(temp_name).unlink(missing_ok=True)
            Path(temp_name + ".wal").unlink(missing_ok=True)

    return probe


def main() -> None:
    parser = argparse.ArgumentParser(description="v0.2 联网合并前端到端验收")
    parser.add_argument("--provider", choices=["auto", "eastmoney", "akshare"], default="auto")
    parser.add_argument("--benchmark-provider", choices=["auto", "eastmoney", "akshare"], default="auto")
    parser.add_argument("--benchmark", choices=sorted(BENCHMARKS), default="csi300")
    parser.add_argument("--reference-etf", choices=[""] + sorted(ETFS), default="csi300_etf_sh")
    parser.add_argument("--codes", default="600519,000001,300750")
    parser.add_argument("--db", default="market.duckdb")
    parser.add_argument("--with-baostock", action="store_true")
    parser.add_argument("--skip-duckdb", action="store_true")
    parser.add_argument("--membership-overlap-floor", type=float, default=0.90)
    parser.add_argument("--allow-missing-bj", action="store_true")
    parser.add_argument("--json-out", default="output/live_validation.json")
    args = parser.parse_args()

    market = make_provider(args.provider)
    reference = make_benchmark_provider(args.benchmark_provider)
    master = None
    if args.with_baostock:
        # Split failure domains deliberately:
        # - BaoStock is the proven first choice for SH/SZ history and snapshots.
        # - current identity comes from BaoStock SH/SZ + the BSE official list.
        # - the normal market chain stays available for price fallbacks, including
        #   the independent Tencent 920xxx path for BSE history.
        raw = BaostockSecurityMasterProvider()
        master = CachedSecurityMasterProvider(raw, DuckDBSecuritySnapshotStore(args.db))
        universe = BaostockBseUniverseProvider(master)
        market = ProviderChain([
            RetryingProvider(BaostockHistoryProvider(), attempts=2),
            universe,
            market,
        ])
        # Avoid wasting the live gate on blocked public quote hosts when the
        # independent BaoStock reference path has already proved reachable.
        reference = BenchmarkProviderChain([BaostockBenchmarkProvider(), reference])
    report = run_live_validation(
        market,
        reference,
        benchmark=args.benchmark,
        reference_etf=args.reference_etf,
        representative_codes=tuple(x.strip().zfill(6) for x in args.codes.split(",") if x.strip()),
        security_master_provider=master,
        persistence_probe=None if args.skip_duckdb else duckdb_probe(args.db),
        require_markets=("SH", "SZ") if args.allow_missing_bj else ("SH", "SZ", "BJ"),
        membership_overlap_floor=args.membership_overlap_floor,
    )
    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    raise SystemExit(0 if report.ok else 2)


if __name__ == "__main__":
    main()
