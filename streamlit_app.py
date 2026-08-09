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
from presentation.screening import active_filter_descriptions, apply_active_filters
from scanner.market_scanner import scan_codes, select_rankable_universe, select_universe
from storage.duckdb_store import DuckDBStore

st.set_page_config(page_title="全市场做T扫描器", layout="wide")
st.title("全市场做T扫描器")
st.caption("从整个A股当前股票池出发：全市场快速初筛 → Top N 深度T评分 → 参数主动筛选 → 最终候选榜。T Score描述历史做T适合度，不预测明日涨跌。")

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
flow1, flow2, flow3, flow4 = st.columns(4)
flow1.info("① 全A股股票池\n\n获取当前沪深北证券身份，排除ST后作为扫描母集。")
flow2.info("② 全市场快速初筛\n\n优先按当前成交额/流动性，从全市场选出进入深度评分的股票。")
flow3.info("③ 深度T评分\n\n补齐日K并计算统一T Score，保证不同参数方案之间仍可比较。")
flow4.info("④ 参数主动筛选\n\n只启用你勾选的指标和阈值，动态过滤最终候选榜。")

st.markdown("### 扫描参数")
c1, c2, c3 = st.columns(3)
deep_limit = c1.number_input("进入深度T评分数量", min_value=20, max_value=1000, value=200, step=20)
final_top = c2.number_input("最终展示Top", min_value=5, max_value=100, value=20, step=5)
workers = c3.slider("日K并发", 1, 8, 4)

c4, c5 = st.columns(2)
exclude_st = c4.checkbox("排除 ST", value=True)
refresh_universe = c5.checkbox("强制刷新股票池", value=False)

st.caption("这里的“200”不是只扫描200只股票：系统先读取完整A股股票池，再从全市场粗筛出200只进入历史T Score深度评分。")

with st.expander("筛选参数编辑器", expanded=True):
    st.caption("勾选=该指标参与主动筛选；取消勾选=该指标完全不限制结果。调整这些参数不会修改T Score公式，只改变最终候选过滤条件。")

    st.markdown("#### 第一阶段：全市场粗筛")
    pre1, pre2 = st.columns([1, 2])
    use_spot_amount = pre1.checkbox("启用实时成交额下限", value=False)
    spot_amount_yi = pre2.number_input(
        "当前成交额下限（亿）",
        min_value=0.0,
        value=5.0,
        step=1.0,
        disabled=not use_spot_amount,
        help="仅在当前股票池数据源提供实时成交额时生效；不提供时会明确报错，不会假装过滤。",
    )

    st.markdown("#### 第二阶段：T Score结果主动筛选")
    r1c1, r1c2 = st.columns([1, 2])
    enable_score = r1c1.checkbox("启用 T Score", value=True)
    min_score = r1c2.number_input("最低 T Score", min_value=0.0, max_value=100.0, value=60.0, step=1.0, disabled=not enable_score)

    r2c1, r2c2 = st.columns([1, 2])
    enable_amp = r2c1.checkbox("启用平均振幅", value=True)
    amp_range = r2c2.slider("平均振幅区间（%）", 0.0, 15.0, (2.0, 8.0), 0.1, disabled=not enable_amp)

    r3c1, r3c2 = st.columns([1, 2])
    enable_amount = r3c1.checkbox("启用历史中位成交额", value=True)
    min_median_amount_yi = r3c2.number_input("中位成交额下限（亿）", min_value=0.0, value=5.0, step=1.0, disabled=not enable_amount)

    r4c1, r4c2 = st.columns([1, 2])
    enable_space = r4c1.checkbox("启用历史日内空间", value=True)
    intraday_space_range = r4c2.slider("日内空间区间（%）", 0.0, 15.0, (1.5, 8.0), 0.1, disabled=not enable_space)

    r5c1, r5c2 = st.columns([1, 2])
    enable_drawdown = r5c1.checkbox("启用最大回撤", value=True)
    max_drawdown_floor = r5c2.number_input(
        "允许的最大回撤下限（%）",
        min_value=-100.0,
        max_value=0.0,
        value=-35.0,
        step=1.0,
        disabled=not enable_drawdown,
        help="例如 -35 表示只保留最大回撤不差于 -35% 的股票。",
    )

    r6c1, r6c2 = st.columns([1, 2])
    enable_liquidity = r6c1.checkbox("启用流动性得分", value=False)
    min_liquidity_score = r6c2.number_input("最低流动性得分", min_value=0.0, max_value=100.0, value=50.0, step=1.0, disabled=not enable_liquidity)

    r7c1, r7c2 = st.columns([1, 2])
    enable_reversion = r7c1.checkbox("启用均值回归得分", value=False)
    min_reversion_score = r7c2.number_input("最低均值回归得分", min_value=0.0, max_value=100.0, value=45.0, step=1.0, disabled=not enable_reversion)

    r8c1, r8c2 = st.columns([1, 2])
    enable_risk = r8c1.checkbox("启用风险控制得分", value=True)
    min_risk_score = r8c2.number_input("最低风险控制得分", min_value=0.0, max_value=100.0, value=40.0, step=1.0, disabled=not enable_risk)

    r9c1, r9c2 = st.columns([1, 2])
    enable_observations = r9c1.checkbox("启用有效历史样本数", value=True)
    min_observations = r9c2.number_input("最少有效交易日", min_value=20, max_value=120, value=40, step=5, disabled=not enable_observations)

active_filters = {
    "min_score": float(min_score) if enable_score else None,
    "avg_amplitude_range": tuple(float(x) for x in amp_range) if enable_amp else None,
    "min_median_amount": float(min_median_amount_yi) * 1e8 if enable_amount else None,
    "intraday_space_range": tuple(float(x) for x in intraday_space_range) if enable_space else None,
    "max_drawdown_floor": float(max_drawdown_floor) if enable_drawdown else None,
    "min_liquidity_score": float(min_liquidity_score) if enable_liquidity else None,
    "min_mean_reversion_score": float(min_reversion_score) if enable_reversion else None,
    "min_risk_score": float(min_risk_score) if enable_risk else None,
    "min_observations": int(min_observations) if enable_observations else None,
}

active_descriptions = active_filter_descriptions(active_filters)
if active_descriptions:
    st.caption("当前主动筛选：" + " · ".join(active_descriptions))
else:
    st.caption("当前没有启用任何第二阶段筛选指标；所有有效T评分股票都可进入最终排序。")

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
            min_spot_amount=float(spot_amount_yi) * 1e8 if use_spot_amount else 0.0,
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

        status.write("计算统一T Score...")
        scores = scan_codes(codes, provider, lookback=int(lookback), store=store)
        ranking = enrich_t_ranking(scores)
        if ranking.empty:
            raise RuntimeError("没有生成有效T Score结果。")

        valid = ranking[(ranking["score"] > 0) & (ranking["suitability"] != "数据异常")].copy()
        valid = valid.sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)

        st.session_state["full_market_valid_ranking"] = valid
        st.session_state["full_market_scan_meta"] = {
            "total_universe": total_universe,
            "deep_count": len(codes),
            "market_counts": market_counts,
            "selection_basis": basis,
        }
        status.update(label="全市场扫描完成，可实时调整筛选参数", state="complete", expanded=False)

    except Exception as exc:
        st.error(str(exc))
        st.caption("如果提示当前股票池没有实时成交额且没有历史评分缓存，说明云端当前只拿到了证券身份，没有足够的全市场流动性数据做诚实粗筛；系统不会按股票代码顺序随便截取前N只冒充全市场结果。")

if "full_market_valid_ranking" in st.session_state:
    valid = st.session_state["full_market_valid_ranking"].copy()
    meta = st.session_state.get("full_market_scan_meta", {})

    filtered = apply_active_filters(valid, active_filters)
    filtered = filtered.sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)
    filtered["filter_rank"] = range(1, len(filtered) + 1)
    display_top = filtered.head(int(final_top)).copy()

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("全市场母集", f"{int(meta.get('total_universe', 0))}只")
    m2.metric("进入深度评分", f"{int(meta.get('deep_count', 0))}只")
    m3.metric("有效T评分", f"{len(valid)}只")
    m4.metric("参数筛选通过", f"{len(filtered)}只")
    m5.metric("最终候选", f"{len(display_top)}只")

    market_counts = meta.get("market_counts") or {}
    if market_counts:
        parts = [f"{k} {v}只" for k, v in sorted(market_counts.items())]
        st.caption("市场构成：" + " · ".join(parts))

    basis_cn = {
        "current_amount": "当前成交额 / 流动性",
        "cached_t_score": "本地上一次有效T Score缓存",
        "full_universe_no_prefilter": "完整股票池（未做额外粗筛）",
    }.get(meta.get("selection_basis", ""), str(meta.get("selection_basis", "")))
    if basis_cn:
        st.caption(f"第一阶段粗筛依据：{basis_cn}")

    if active_descriptions:
        st.info("当前参数方案：" + "；".join(active_descriptions))

    if display_top.empty:
        st.warning("当前参数组合没有筛出候选。可以放宽任意已启用指标，结果会立即重新过滤，无需重新下载行情。")
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
            [
                "filter_rank", "rank", "code", "name", "score", "grade", "suitability", "risk_label",
                "avg_amplitude", "avg_intraday_space", "median_amount_yi", "max_drawdown",
                "liquidity_score", "mean_reversion_score", "risk_score", "observations", "reason", "provider",
            ]
        ].rename(columns={
            "filter_rank": "筛选后排名",
            "rank": "原T排名",
            "code": "代码",
            "name": "名称",
            "score": "T Score",
            "grade": "等级",
            "suitability": "适合度",
            "risk_label": "风险",
            "avg_amplitude": "平均振幅%",
            "avg_intraday_space": "日内空间%",
            "median_amount_yi": "中位成交额(亿)",
            "max_drawdown": "最大回撤%",
            "liquidity_score": "流动性得分",
            "mean_reversion_score": "均值回归得分",
            "risk_score": "风险控制得分",
            "observations": "有效样本日",
            "reason": "为什么",
            "provider": "数据源",
        })
        st.dataframe(table, use_container_width=True, hide_index=True)
        st.download_button(
            "下载当前参数筛选结果 CSV",
            filtered.to_csv(index=False, encoding="utf-8-sig"),
            "full_market_t_candidates_filtered.csv",
            "text/csv",
            use_container_width=True,
        )

st.divider()
st.markdown("### 参数编辑器怎么用")
st.caption("先运行一次全市场扫描获得深度评分池。之后勾选/取消筛选指标或修改阈值，会直接对已评分结果重新过滤，不需要重复下载历史行情。只有修改“进入深度T评分数量”、数据源、回看周期等扫描参数时才需要重新扫描。")
st.caption("全市场扫描的核心是“母集覆盖全A股”，不是给5500多只股票全部下载5分钟数据。分钟级深挖应只对最终Top候选执行，以减少限流和无效请求。")
st.caption("本工具用于历史行情研究和候选筛选，不构成投资建议；T Score不是涨跌预测。")
