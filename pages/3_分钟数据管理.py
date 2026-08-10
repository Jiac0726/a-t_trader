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

import streamlit as st

from app.cli import make_raw_provider
from data.hydrator import hydrate_codes
from providers.tencent_history import TencentHistoryProvider
from storage.duckdb_store import DuckDBStore


st.set_page_config(page_title="分钟数据管理", layout="wide")
st.title("分钟数据管理")
st.caption("5分钟数据只给候选股、自选股或准备回测的股票建库，不对全A股做无意义的分钟级全量抓取。")

db_path = st.sidebar.text_input("行情数据库", "market.duckdb", key="minute_db")
store = DuckDBStore(db_path)

source_map = {
    "自动降级": "auto",
    "腾讯历史": "tencent",
    "东方财富": "eastmoney",
    "AKShare": "akshare",
    "Tushare（需Token）": "tushare",
    "离线演示": "demo",
}
source_label = st.selectbox("分钟历史更新源", list(source_map), index=0)


def factory(name: str):
    if name == "tencent":
        return TencentHistoryProvider()
    return make_raw_provider(name)


def default_codes() -> str:
    ranking = st.session_state.get("full_market_valid_ranking")
    if ranking is not None and not ranking.empty and "code" in ranking.columns:
        return "\n".join(ranking.head(20)["code"].astype(str).str.zfill(6).tolist())
    return "300059\n601899"


codes_text = st.text_area("股票代码（每行一个）", default_codes(), height=180)
c1, c2, c3 = st.columns(3)
start_date = c1.date_input("起始日", date.today() - timedelta(days=90))
end_date = c2.date_input("结束日", date.today())
interval = c3.selectbox("分钟周期", ["5m", "1m", "15m", "30m", "60m"], index=0)

c4, c5, c6 = st.columns(3)
workers = c4.slider("并发", 1, 6, 2)
rps = c5.slider("总请求/秒", 0.5, 5.0, 1.5, 0.5)
retries = c6.slider("失败重试", 0, 3, 1)

st.info("分钟库始终保存不复权名义价格（adjust=none），用于真实股数、佣金、印花税和滑点回测。")

if st.button("补齐分钟历史", type="primary", width="stretch"):
    codes = list(dict.fromkeys(x.strip().zfill(6) for x in codes_text.splitlines() if x.strip()))
    if not codes:
        st.error("请输入至少一只股票。")
    else:
        status = st.status(f"正在补齐 {len(codes)} 只股票的 {interval} 历史...", expanded=True)
        try:
            report = hydrate_codes(
                codes,
                provider_factory=lambda n=source_map[source_label]: factory(n),
                store=store,
                start=start_date,
                end=end_date,
                interval=interval,
                adjust="none",
                workers=int(workers),
                requests_per_second=float(rps),
                retries=int(retries),
            )
            status.write(
                f"请求区间 {report.requested_ranges}，成功 {report.succeeded_ranges}，"
                f"失败 {report.failed_ranges}，新取 {report.fetched_rows:,} 行。"
            )
            if report.failures:
                for item in report.failures[:10]:
                    status.write(f"{item.code} {item.start}→{item.end}: {item.error}")
            status.update(
                label=f"分钟数据更新完成：新增 {report.fetched_rows:,} 行，失败 {report.failed_ranges}",
                state="complete" if report.failed_ranges == 0 else "error",
                expanded=report.failed_ranges > 0,
            )
        except Exception as exc:
            status.update(label="分钟数据更新失败", state="error", expanded=True)
            st.error(str(exc))

st.markdown("### 本地覆盖检查")
check_code = st.text_input("检查股票", "300059", max_chars=6)
if st.button("检查本地分钟覆盖"):
    low, high = store.history_bounds(check_code, interval, adjust="none")
    if low is None:
        st.warning(f"{check_code} 暂无本地 {interval} 数据。")
    else:
        rows = store.load_history(check_code, interval, low, high, adjust="none")
        st.success(f"{check_code} · {interval} · {low} → {high} · {len(rows):,}行")

st.divider()
st.caption("原则：日K做全市场数据库；分钟数据按候选股/回测需求建立，避免请求量和存储量失控。")
