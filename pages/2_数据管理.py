from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)
os.chdir(ROOT)

import pandas as pd
import streamlit as st

from app.cli import make_raw_provider, make_universe_provider
from data.hydrator import hydrate_codes
from providers.tencent_history import TencentHistoryProvider
from storage.market_database import MarketDatabase


st.set_page_config(page_title="数据管理", layout="wide")
st.title("数据管理")
st.caption("公网只负责建库和增量更新；全市场扫描、单票分析和回测默认只读取本地 DuckDB。")

DB_PATH = st.sidebar.text_input("行情数据库", "market.duckdb", key="data_db_path")
db = MarketDatabase(DB_PATH)
store = db.store

st.warning(
    "Streamlit Community Cloud 的本地磁盘不是长期持久存储。这个数据管理页适合本机或自有服务器；"
    "云端演示实例重建后 market.duckdb 可能丢失。正式使用建议把程序和 DuckDB 放在自己的电脑或国内服务器。"
)

stats = db.stats()
m1, m2, m3, m4 = st.columns(4)
m1.metric("股票池", f"{stats.universe_rows}只")
m2.metric("原始日K", f"{stats.raw_daily_codes}只 / {stats.raw_daily_rows:,}行")
m3.metric("前复权日K", f"{stats.qfq_daily_codes}只 / {stats.qfq_daily_rows:,}行")
m4.metric("数据库大小", f"{stats.file_size_mb:.2f} MB")

st.caption(
    f"市场：SH {stats.sh_rows} · SZ {stats.sz_rows} · BJ {stats.bj_rows}　|　"
    f"raw：{stats.raw_start or '-'} → {stats.raw_end or '-'}　|　"
    f"qfq：{stats.qfq_start or '-'} → {stats.qfq_end or '-'}"
)


def history_factory(name: str):
    if name == "tencent":
        return TencentHistoryProvider()
    return make_raw_provider(name)


def selected_codes(markets: list[str], exclude_st: bool, limit: int) -> list[str]:
    stocks = store.load_stock_list()
    if stocks is None or stocks.empty:
        raise RuntimeError("本地股票池为空，请先更新股票池。")
    x = stocks.copy()
    x["code"] = x["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    if "market" in x.columns and markets:
        x = x[x["market"].astype(str).str.upper().isin(markets)]
    if exclude_st and "name" in x.columns:
        x = x[~x["name"].astype(str).str.upper().str.contains("ST", na=False)]
    x = x.drop_duplicates("code").sort_values("code")
    if int(limit) > 0:
        x = x.head(int(limit))
    return x["code"].tolist()


def run_update(
    *,
    run_type: str,
    codes: list[str],
    start,
    end,
    provider_name: str,
    do_raw: bool,
    do_qfq: bool,
    workers: int,
    rps: float,
    retries: int,
):
    if not codes:
        raise RuntimeError("没有可更新股票。")
    if not do_raw and not do_qfq:
        raise RuntimeError("至少选择 raw 或 qfq 一种历史数据。")

    run_id = db.begin_update_run(run_type, len(codes))
    raw_rows = qfq_rows = failures = 0
    messages: list[str] = []
    status = st.status(f"{run_type}：准备更新 {len(codes)} 只股票...", expanded=True)
    try:
        for adjust, label in [("none", "原始日K"), ("qfq", "前复权日K")]:
            if adjust == "none" and not do_raw:
                continue
            if adjust == "qfq" and not do_qfq:
                continue
            status.write(f"更新 {label}：{pd.Timestamp(start).date()} → {pd.Timestamp(end).date()}")
            report = hydrate_codes(
                codes,
                provider_factory=lambda n=provider_name: history_factory(n),
                store=store,
                start=start,
                end=end,
                interval="1d",
                adjust=adjust,
                workers=int(workers),
                requests_per_second=float(rps),
                retries=int(retries),
            )
            if adjust == "none":
                raw_rows += report.fetched_rows
            else:
                qfq_rows += report.fetched_rows
            failures += report.failed_ranges
            messages.append(
                f"{label}: 请求区间 {report.requested_ranges}, 成功 {report.succeeded_ranges}, "
                f"失败 {report.failed_ranges}, 新取 {report.fetched_rows} 行"
            )
            status.write(messages[-1])
            if report.failures:
                sample = "；".join(f"{x.code}: {x.error}" for x in report.failures[:5])
                status.write("失败样例：" + sample)

        final_status = "success" if failures == 0 else "partial"
        db.finish_update_run(
            run_id,
            status=final_status,
            raw_rows=raw_rows,
            qfq_rows=qfq_rows,
            failures=failures,
            message=" | ".join(messages),
        )
        status.update(
            label=f"{run_type}完成：raw +{raw_rows:,} 行，qfq +{qfq_rows:,} 行，失败区间 {failures}",
            state="complete" if failures == 0 else "error",
            expanded=failures > 0,
        )
    except Exception as exc:
        db.finish_update_run(run_id, status="failed", raw_rows=raw_rows, qfq_rows=qfq_rows, failures=failures + 1, message=str(exc))
        status.update(label=f"{run_type}失败", state="error", expanded=True)
        raise


st.markdown("### ① 股票池")
universe_source_map = {
    "自动降级": "auto",
    "东方财富": "eastmoney",
    "AKShare": "akshare",
    "Tushare（需Token）": "tushare",
    "离线演示": "demo",
}
universe_label = st.selectbox("股票池更新源", list(universe_source_map), index=0)
if st.button("更新股票池", type="primary", width="stretch"):
    try:
        provider = make_universe_provider(universe_source_map[universe_label], retries=1)
        with st.spinner("获取当前证券身份并写入本地数据库..."):
            stocks = provider.stock_list()
            count = db.sync_universe(stocks, source=stocks.attrs.get("provider", provider.name))
        st.success(f"股票池已写入本地数据库：{count}只。以后正常扫描直接读取本地，不再请求股票列表接口。")
        st.rerun()
    except Exception as exc:
        st.error(str(exc))
        st.info("如果云端多个股票池源都受限，可在本机运行本页完成一次股票池建库，再长期使用本地数据库。")

st.markdown("### ② 首次历史建库")
st.caption("首次建库可以耗时较长，但只做一次。之后“每日增量更新”只补缺失尾部。raw 是长期原始数据；qfq 是做T评分的快速材料化缓存。")

c1, c2, c3 = st.columns(3)
init_start = c1.date_input("历史起始日", date.today() - timedelta(days=365 * 3), key="init_start")
markets = c2.multiselect("市场", ["SH", "SZ", "BJ"], default=["SH", "SZ", "BJ"], key="init_markets")
init_limit = c3.number_input("本次最多股票（0=全部）", min_value=0, max_value=6000, value=0, step=100, key="init_limit")

h1, h2, h3, h4 = st.columns(4)
history_source_map = {
    "自动降级": "auto",
    "腾讯历史": "tencent",
    "东方财富": "eastmoney",
    "AKShare": "akshare",
    "Tushare（需Token）": "tushare",
    "离线演示": "demo",
}
history_label = h1.selectbox("历史行情更新源", list(history_source_map), index=0)
workers = h2.slider("并发", 1, 8, 3)
rps = h3.slider("总请求/秒", 0.5, 6.0, 2.0, 0.5)
retries = h4.slider("失败重试", 0, 3, 1)

f1, f2, f3 = st.columns(3)
do_raw = f1.checkbox("保存 raw 原始日K", value=True)
do_qfq = f2.checkbox("保存 qfq 前复权评分缓存", value=True)
exclude_st = f3.checkbox("建库时排除 ST", value=False)

if st.button("初始化历史数据库", type="primary", width="stretch"):
    try:
        codes = selected_codes(markets, exclude_st, int(init_limit))
        run_update(
            run_type="initialize",
            codes=codes,
            start=init_start,
            end=date.today(),
            provider_name=history_source_map[history_label],
            do_raw=do_raw,
            do_qfq=do_qfq,
            workers=workers,
            rps=rps,
            retries=retries,
        )
        st.success("初始化任务完成。正常扫描现在可以完全脱离历史行情公网接口。")
    except Exception as exc:
        st.error(str(exc))

st.markdown("### ③ 每日增量更新")
st.caption("每天收盘后执行一次即可。系统根据 history_coverage 自动判断缺口；已有历史不会重新整段下载。qfq 会保留小段重叠用于识别除权除息导致的复权尺度变化。")
inc_days = st.slider("向前检查自然日", 7, 45, 20, 1)
if st.button("执行每日增量更新", width="stretch"):
    try:
        codes = selected_codes(["SH", "SZ", "BJ"], False, 0)
        run_update(
            run_type="incremental",
            codes=codes,
            start=date.today() - timedelta(days=int(inc_days)),
            end=date.today(),
            provider_name=history_source_map[history_label],
            do_raw=True,
            do_qfq=True,
            workers=workers,
            rps=rps,
            retries=retries,
        )
        st.success("增量更新完成。")
    except Exception as exc:
        st.error(str(exc))

st.markdown("### ④ 指定股票修复")
r1, r2 = st.columns([2, 1])
repair_text = r1.text_area("股票代码（每行一个）", "300059\n601899", height=100)
repair_adjust = r2.multiselect("修复数据", ["raw", "qfq"], default=["raw", "qfq"])
r3, r4, r5 = st.columns(3)
repair_start = r3.date_input("修复起始日", date.today() - timedelta(days=365), key="repair_start")
repair_end = r4.date_input("修复结束日", date.today(), key="repair_end")
clear_before = r5.checkbox("先清空指定区间所属缓存命名空间", value=False, help="仅在确认某只股票历史数据整体污染时使用。")

if st.button("修复指定股票", width="stretch"):
    try:
        codes = [x.strip().zfill(6) for x in repair_text.splitlines() if x.strip()]
        if clear_before:
            for code in codes:
                if "raw" in repair_adjust:
                    store.clear_history(code, "1d", adjust="none")
                if "qfq" in repair_adjust:
                    store.clear_history(code, "1d", adjust="qfq")
        run_update(
            run_type="repair",
            codes=codes,
            start=repair_start,
            end=repair_end,
            provider_name=history_source_map[history_label],
            do_raw="raw" in repair_adjust,
            do_qfq="qfq" in repair_adjust,
            workers=min(int(workers), max(1, len(codes))),
            rps=rps,
            retries=retries,
        )
        st.success("指定股票修复完成。")
    except Exception as exc:
        st.error(str(exc))

st.divider()
st.caption("原则：公网是数据补给源，不是运行依赖。数据库建好后，即使 BaoStock / 东财 / 交易所接口临时不可用，历史评分与回测仍可正常运行。")
