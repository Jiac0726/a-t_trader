from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from app.cli import make_provider
from data.cached_security_master import CachedSecurityMasterProvider
from data.hydrator import hydrate_codes
from dataset.builder import (
    ResearchDatasetBuild,
    attach_minute_labels_from_store,
    build_daily_score_panel,
    minute_coverage_report,
    select_minute_candidates,
    universe_codes_from_lifecycle,
)
from providers.baostock_master import BaostockSecurityMasterProvider
from storage.duckdb_store import DuckDBStore
from storage.security_snapshot_store import DuckDBSecuritySnapshotStore


def main() -> None:
    p = argparse.ArgumentParser(description="构建可断点续跑的A股历史横截面研究数据集")
    p.add_argument("--provider", choices=["auto", "eastmoney", "akshare", "demo"], default="auto")
    p.add_argument("--db", default="market.duckdb")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--lookback", type=int, default=60)
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--limit", type=int, default=0, help="研究/联调时限制股票数量；0=不限制")
    p.add_argument("--minute-limit", type=int, default=0, help="本轮分钟数据最多处理多少只；0=只使用已有分钟缓存")
    p.add_argument("--minute-days", type=int, default=120, help="分钟请求的日历回看窗口；实际可得范围以返回数据为准")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--rps", type=float, default=3.0)
    p.add_argument("--with-baostock", action="store_true")
    p.add_argument("--exact-snapshots", action="store_true", help="用每个评分交易日的BaoStock实际证券快照做精确点时过滤")
    p.add_argument("--output-dir", default="output/dataset")
    args = p.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store = DuckDBStore(args.db)
    master = None
    snapshots = None
    security = None

    if args.with_baostock:
        security = CachedSecurityMasterProvider(BaostockSecurityMasterProvider(), DuckDBSecuritySnapshotStore(args.db))
        master = security.master()
        codes = universe_codes_from_lifecycle(master, args.start, args.end)
    else:
        stocks = make_provider(args.provider).stock_list()
        codes = stocks["code"].astype(str).str.zfill(6).tolist()

    if args.limit > 0:
        codes = codes[: args.limit]
    if not codes:
        raise SystemExit("没有可用于构建数据集的股票代码")

    provider_factory = lambda: make_provider(args.provider)
    daily_report = hydrate_codes(
        codes,
        provider_factory,
        store,
        args.start,
        args.end,
        interval="1d",
        workers=args.workers,
        requests_per_second=args.rps,
    )

    daily_panel, score_failures = build_daily_score_panel(
        codes,
        store,
        start=args.start,
        end=args.end,
        lookback=args.lookback,
        master=master,
        snapshots=None,
    )

    if args.exact_snapshots:
        if security is None:
            raise SystemExit("--exact-snapshots 需要同时启用 --with-baostock")
        score_dates = sorted(pd.to_datetime(daily_panel["date"]).dt.normalize().unique()) if not daily_panel.empty else []
        snapshots = security.snapshot_many(score_dates)
        # Re-run only the membership filter; score computation remains unchanged.
        from providers.security_master import filter_panel_by_snapshots
        daily_panel = filter_panel_by_snapshots(
            daily_panel, snapshots, include_suspended=False, unknown_dates="drop"
        )

    minute_codes: list[str] = []
    minute_report = None
    if args.minute_limit > 0:
        # Stage-2 minute hydration is deliberately bounded and ranked by the
        # latest daily score, with liquidity as a deterministic tie-breaker.
        minute_codes = select_minute_candidates(daily_panel, args.minute_limit)
        minute_start = max(pd.Timestamp(args.start), pd.Timestamp(args.end) - pd.Timedelta(days=args.minute_days))
        if minute_codes:
            minute_report = hydrate_codes(
                minute_codes,
                provider_factory,
                store,
                minute_start,
                args.end,
                interval="5m",
                workers=max(1, min(args.workers, 4)),
                requests_per_second=min(args.rps, 2.0),
            )
    else:
        minute_codes = sorted(daily_panel["code"].unique()) if not daily_panel.empty else []

    labeled, label_failures = attach_minute_labels_from_store(
        daily_panel,
        store,
        codes=minute_codes,
        horizon=args.horizon,
    )
    coverage = minute_coverage_report(minute_codes, store)

    def write_panel(frame: pd.DataFrame, stem: str) -> str:
        parquet = out_dir / f"{stem}.parquet"
        try:
            frame.to_parquet(parquet, index=False)
            return parquet.name
        except (ImportError, ModuleNotFoundError):
            csv = out_dir / f"{stem}.csv"
            frame.to_csv(csv, index=False, encoding="utf-8-sig")
            return csv.name

    daily_panel_file = write_panel(daily_panel, "daily_score_panel")
    labeled_panel_file = write_panel(labeled, "labeled_panel")
    coverage.to_csv(out_dir / "minute_coverage.csv", index=False, encoding="utf-8-sig")

    failures = [*score_failures, *label_failures]
    build = ResearchDatasetBuild(
        requested_codes=len(codes),
        daily_ready_codes=int(daily_panel["code"].nunique()) if not daily_panel.empty else 0,
        minute_ready_codes=int((coverage["rows_5m"] > 0).sum()) if not coverage.empty else 0,
        panel_rows=len(daily_panel),
        labeled_rows=int(labeled["forward_opportunity_pct"].notna().sum()) if not labeled.empty and "forward_opportunity_pct" in labeled.columns else 0,
        failures=failures,
    )
    payload = {
        "build": build.to_dict(),
        "daily_hydration": daily_report.to_dict(),
        "minute_hydration": None if minute_report is None else minute_report.to_dict(),
        "minute_coverage_rows": len(coverage),
        "daily_panel_file": daily_panel_file,
        "labeled_panel_file": labeled_panel_file,
        "universe_filter": "exact_snapshots" if args.exact_snapshots else ("lifecycle" if master is not None else "current_provider"),
        "snapshot_rows": 0 if snapshots is None else int(len(snapshots)),
    }
    (out_dir / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
