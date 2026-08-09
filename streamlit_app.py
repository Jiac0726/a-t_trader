from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)
os.chdir(ROOT)

import pandas as pd
import streamlit as st

from app.cli import make_provider, make_raw_provider
from data.cached_provider import CachedProvider
from data.hydrator import hydrate_codes
from presentation.ranking import enrich_t_ranking
from scanner.market_scanner import scan_codes, select_rankable_universe, select_universe
from storage.duckdb_store import DuckDBStore

st.set_page_config(page_title="全市场做T扫描器", layout="wide")
st.title("全市场做T扫描器")
st.caption("从整个A股当前股票池出发：全市场快速初筛 → Top N 深度T评分 → 最终候选榜。T Score描述历史做T适合度，不预测明日涨跌。")

SOURCE_MAP = {
    "自动降级（推荐）": "auto",
    "东方财富": "eastmoney",
    "AKShare": "akshare",
    "离线演示": "demo",
}

provider_choice = st.sidebar.selectbox("数据源", list(SOURCE_MAP), index=0)
lookback = st.sidebar.slider("T Score回看交易日", 20, 120, 60, 10)
use_cache = st.sidebar.checkbox("启用 DuckDB 缓存", value=True)
db_path = st.sidebar.text_input("缓存数据库", "market.duckdb", disabled=not use_cache)
retries = st.sidebar.slider("失败重试次数", 0, 4, 2)


def provider_key() -> str:
    return SOURCE_MAP[provider_choice]


def provider_and_store():
    raw = make_provider(provider_key(), retries=retries)
    if not use_cache:
        return raw, None
    store = DuckDBStore(db_path)
    return CachedProvider(raw, store), store


def raw_provider_factory():
    return make_raw_provider(provider_key())


st.markdown("### 扫描流程")
flow1, flow2, flow3 = st.columns(3)
flow1.info("① 全A股股票池\n\n获取当前沪深北证券身份，排除ST后作为扫描母集。")
flow2.info("② 全市场快速初筛\n\n优先按当前成交额/流动性从全市场选出进入深度评分的股票。")
flow3.info("③ 深度T评分\n\n补齐日K、计算T Score，输出最终Top候选；分钟数据只留给最前面的股票。")

c1, c2, c3, c4 = st.columns(4)
deep_limit = c1.number_input("进入深度T评分数量", min_value=20, max_value=1000, value=200, step=20)
final_top = c2.number_input("最终展示Top", min_value=5, max_value=100, value=20, step=5)
min_amount_yi = c3.number_input("成交额下限(亿，0=关闭)", min_value=0.0, value=0.0, step=1.0)
workers = c4.slider("日K并发", 1, 8, 4)

c5, c6 = st.columns(2)
exclude_st = c5.checkbox("排除 ST", value=True)
refresh_universe = c6.checkbox("强制刷新股票池", value=False)

st.caption("这里的“200”不是只扫描200只股票，而是：先读取完整A股股票池并做第一阶段粗筛，再让其中200只进入历史T Score深度评分。")

if st.button("开始全市场做T扫描", type="primary", use_container_width=True):
    try:
        provider, store = provider_and_store()
        if refresh_universe and isinstance(provider, CachedProvider):
            provider.refresh_stock_list()

        status = st.status("正在执行全市场扫描...", expanded=True)

        status.write("读取当前A股完整股票池...")
        full_universe = select_universe(provider, limit=None, exclude_st=exclude_st, min_spot_amount=0.0)
        total_universe = len(full_universe)
        if total_universe == 0:
            raise RuntimeError("当前数据源没有返回A股股票池。")

        market_counts = {}
        if "market" in full_universe.columns:
            market_counts = full_universe["market"].astype(str).value_counts().to_dict()

        status.write(f"全市场母集：{total_universe}只；开始第一阶段粗筛...")
        candidates = select_rankable_universe(
            provider,
            store=store,
            limit=int(deep_limit),
            exclude_st=exclude_st,
            min_spot_amount=float(min_amount_yi) * 1e8,
        )
        basis = candidates.attrs.get("selection_basis", "unknown")
        codes = candidates["code"].astype(str).tolist() if not candidates.empty else []
        if not codes:
            raise RuntimeError("全市场粗筛后没有可进入深度评分的股票。")

        status.write(f"第一阶段完成：从{total_universe}只中选出{len(codes)}只进入历史T Score深度评分。")

        if store is not None and workers > 1:
            end = date.today()
            status.write("并发补齐候选股票日K缓存...")
            hydration = hydrate_codes(
                codes,
                provider_factory=raw_provider_factory,
                store=store,
                start=end - timedelta(days=140),
                end=end,
                interval="1d",
                adjust="qfq",
                workers=int(workers),
                requests_per_second=4.0,
                retries=int(retries),
            )
            if hydration.failed_ranges:
                status.write(f"注意：{hydration.failed_ranges}个历史区间补齐失败，将继续使用可用缓存/备用数据源评分。")

        status.write("计算T Score并生成候选榜...")
        scores = scan_codes(codes, provider, lookback=int(lookback), store=store)
        ranking = enrich_t_ranking(scores)
        if ranking.empty:
            raise RuntimeError("没有生成有效T Score结果。")

        valid = ranking[(ranking["score"] > 0) & (ranking["suitability"] != "数据异常")].copy()
        valid = valid.sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)
        display_top = valid.head(int(final_top)).copy()
        status.update(label="全市场扫描完成", state="complete", expanded=False)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("全市场母集", f"{total_universe}只")
        m2.metric("进入深度评分", f"{len(codes)}只")
        m3.metric("有效T评分", f"{len(valid)}只")
        m4.metric("最终候选", f"{len(display_top)}只")

        if market_counts:
            parts = [f"{k} {v}只" for k, v in sorted(market_counts.items())]
            st.caption("市场构成：" + " · ".join(parts))

        basis_cn = {
            "current_amount": "当前成交额 / 流动性",
            "cached_t_score": "本地上一次有效T Score缓存",
            "full_universe_no_prefilter": "完整股票池（未做额外粗筛）",
        }.get(basis, basis)
        st.caption(f"第一阶段粗筛依据：{basis_cn}")

        if display_top.empty:
            st.warning("深度评分已完成，但当前没有达到有效展示条件的候选。")
        else:
            top = display_top.iloc[0]
            a, b, c, d = st.columns(4)
            a.metric("当前第一候选", f"{top.get('name', '')} {top['code']}".strip())
            b.metric("T Score", f"{float(top['score']):.1f}")
            c.metric("适合度", str(top["suitability"]))
            d.metric("风险", str(top["risk_label"]))
            st.info(f"第一候选原因：{top['reason']}")

            display_top["median_amount_yi"] = pd.to_numeric(display_top["median_amount"], errors="coerce").fillna(0) / 1e8
            table = display_top[
                ["rank", "code", "name", "score", "grade", "suitability", "risk_label", "avg_amplitude", "avg_intraday_space", "median_amount_yi", "reason", "provider"]
            ].rename(columns={
                "rank": "排名",
                "code": "代码",
                "name": "名称",
                "score": "T Score",
                "grade": "等级",
                "suitability": "适合度",
                "risk_label": "风险",
                "avg_amplitude": "平均振幅%",
                "avg_intraday_space": "日内空间%",
                "median_amount_yi": "中位成交额(亿)",
                "reason": "为什么",
                "provider": "数据源",
            })
            st.dataframe(table, use_container_width=True, hide_index=True)
            st.download_button(
                "下载全市场做T候选榜 CSV",
                display_top.to_csv(index=False, encoding="utf-8-sig"),
                "full_market_t_candidates.csv",
                "text/csv",
                use_container_width=True,
            )
            st.session_state["latest_ranking"] = display_top

    except Exception as exc:
        st.error(str(exc))
        st.caption("如果提示当前股票池没有实时成交额且没有历史评分缓存，说明云端当前只拿到了证券身份，没有足够的全市场流动性数据做诚实粗筛；系统不会按股票代码顺序随便截取前N只冒充全市场结果。")

st.divider()
st.markdown("### 说明")
st.caption("全市场扫描的核心是“母集覆盖全A股”，不是给5500多只股票全部下载5分钟数据。分钟级深挖应只对最终Top候选执行，以减少限流和无效请求。")
st.caption("本工具用于历史行情研究和候选筛选，不构成投资建议；T Score不是涨跌预测。")
