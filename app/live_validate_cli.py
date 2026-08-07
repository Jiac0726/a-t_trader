from __future__ import annotations
import argparse, json
import pandas as pd
from app.cli import make_provider
from data.cached_security_master import CachedSecurityMasterProvider
from providers.baostock_master import BaostockSecurityMasterProvider
from providers.benchmark import AkshareBenchmarkProvider, BenchmarkProviderChain, EastmoneyBenchmarkProvider, BENCHMARKS
from storage.security_snapshot_store import DuckDBSecuritySnapshotStore
from storage.duckdb_store import DuckDBStore
from validation.live import run_live_validation


def duckdb_probe(path: str):
    def probe():
        store=DuckDBStore(path)
        sample=pd.DataFrame({"datetime":[pd.Timestamp("2026-01-02")],"open":[1.0],"high":[1.1],"low":[0.9],"close":[1.0],"volume":[100.0],"amount":[1000.0]})
        store.save_history("999999","1d",sample)
        out=store.load_history("999999","1d")
        if len(out)!=1: raise RuntimeError(f"DuckDB roundtrip rows={len(out)}")
        return "history write/read roundtrip OK", {"rows":len(out)}
    return probe


def main():
    p=argparse.ArgumentParser(description="v0.2 联网合并前端到端验收")
    p.add_argument("--provider", choices=["auto","eastmoney","akshare"], default="auto")
    p.add_argument("--benchmark-provider", choices=["auto","eastmoney","akshare"], default="auto")
    p.add_argument("--benchmark", choices=sorted(BENCHMARKS), default="csi300")
    p.add_argument("--codes", default="600519,000001,300750")
    p.add_argument("--db", default="market.duckdb")
    p.add_argument("--with-baostock", action="store_true")
    p.add_argument("--skip-duckdb", action="store_true")
    p.add_argument("--json-out", default="output/live_validation.json")
    args=p.parse_args()
    market=make_provider(args.provider)
    bench={"eastmoney":EastmoneyBenchmarkProvider(),"akshare":AkshareBenchmarkProvider()}.get(args.benchmark_provider,BenchmarkProviderChain())
    master=None
    if args.with_baostock:
        raw=BaostockSecurityMasterProvider()
        master=CachedSecurityMasterProvider(raw,DuckDBSecuritySnapshotStore(args.db))
    report=run_live_validation(market,bench,benchmark=args.benchmark,representative_codes=tuple(x.strip().zfill(6) for x in args.codes.split(",") if x.strip()),security_master_provider=master,persistence_probe=None if args.skip_duckdb else duckdb_probe(args.db))
    payload=report.to_dict()
    print(json.dumps(payload,ensure_ascii=False,indent=2,default=str))
    from pathlib import Path
    Path(args.json_out).parent.mkdir(parents=True,exist_ok=True)
    Path(args.json_out).write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    raise SystemExit(0 if report.ok else 2)

if __name__=="__main__": main()
