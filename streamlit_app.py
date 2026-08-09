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

from app.cli import make_provider, make_raw_provider, make_universe_provider
from data.cached_provider import CachedProvider
from data.hydrator import hydrate_codes
from presentation.ranking import enrich_t_ranking
from presentation.screening import active_filter_descriptions, apply_active_filters
from providers.tencent_spot import TencentSpotProvider
from scanner.market_scanner import scan_codes, select_universe
from scanner.parameter_filters import SpotFilterConfig, apply_spot_filters
from storage.duckdb_store import DuckDBStore

st.set_page_config(page_title="全市场做T扫描器", layout="wide")
st.title("全市场做T扫描器")
st.caption("全市场证券母集 → 可调实时指标初筛 → Top N 历史T评分 → 可调历史指标筛选 → 最终候选榜。参数只控制筛选，不修改T Score公式。")

SOURCE_MAP = {
    "自动降级（推荐）": "auto",
    "东方财富": "eastmoney",
    "AKShare": "akshare",
    "离线演示": "demo",
}

provider_choice = st.sidebar.selectbox("历史行情数据源", list(SOURCE_MAP), index=0)
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


def universe_provider():
    return make_universe_provider(provider_key(), retries=retries)


st.markdown("### 扫描流程")
flow1, flow2, flow3, flow4 = st.columns(4)
flow1.info("① 股票池\n\n独立证券身份链获取沪/深/北当前股票母集。")
flow2.info("② 实时参数初筛\n\n批量补成交额、换手率、当日振幅、涨跌幅、价格，并按你的阈值主动过滤。")
flow3.info("③ 历史T评分\n\n只给初筛后的Top N补日K并计算统一T Score。")
flow4.info("④ 历史参数筛选\n\n继续按T Score、历史振幅、日内空间、回撤等参数过滤最终候选。")

st.markdown("### 扫描规模")
c1, c2, c3 = st.columns(3)
deep_limit = c1.number_input("进入历史T评分数量", min_value=20, max_value=1500, value=200, step=20)
final_top = c2.number_input("最终展示Top", min_value=5, max_value=200, value=20, step=5)
workers = c3.slider("日K并发", 1, 8, 4)
st.caption("“进入历史T评分200只”表示：先扫描整个可用股票母集的实时指标，再从符合第一阶段参数的股票中取前200只做历史深度评分。")

with st.expander("筛选参数编辑器", expanded=True):
    st.caption("每个开关都是真实筛选条件：关闭后该指标不参与过滤。修改第二阶段参数无需重新下载行情；修改第一阶段参数后点击扫描重新生成深度评分池。")

    st.markdown("#### 第一阶段：全市场实时指标")
    market_options = ["SH", "SZ", "BJ"]
    selected_markets = st.multiselect("市场范围", market_options, default=market_options, help="若云端某市场身份源不可达，页面会明确提示未覆盖，不会假装全市场。")
    exclude_st = st.checkbox("排除 ST", value=True)

    p1a, p1b = st.columns([1, 2])
    enable_spot_amount = p1a.checkbox("成交额", value=True, key="f_spot_amount")
    min_spot_amount_yi = p1b.number_input("当前成交额下限（亿）", min_value=0.0, value=2.0, step=1.0, disabled=not enable_spot_amount)

    p2a, p2b = st.columns([1, 2])
    enable_turnover = p2a.checkbox("换手率", value=False, key="f_turnover")
    min_turnover = p2b.number_input("最低换手率（%）", min_value=0.0, value=0.5, step=0.1, disabled=not enable_turnover)

    p3a, p3b = st.columns([1, 2])
    enable_spot_amp = p3a.checkbox("当日振幅", value=False, key="f_spot_amp")
    spot_amp_range = p3b.slider("当日振幅区间（%）", 0.0, 30.0, (1.0, 12.0), 0.1, disabled=not enable_spot_amp)

    p4a, p4b = st.columns([1, 2])
    enable_pct = p4a.checkbox("当日涨跌幅", value=False, key="f_pct")
    pct_range = p4b.slider("涨跌幅区间（%）", -30.0, 30.0, (-8.0, 8.0), 0.1, disabled=not enable_pct)

    p5a, p5b = st.columns([1, 2])
    enable_price = p5a.checkbox("股价", value=False, key="f_price")
    price_range = p5b.slider("股价区间（元）", 0.0, 1000.0, (2.0, 300.0), 1.0, disabled=not enable_price)

    st.markdown("#### 第二阶段：历史T指标")
    r1c1, r1c2 = st.columns([1, 2])
    enable_score = r1c1.checkbox("T Score", value=True)
    min_score = r1c2.number_input("最低 T Score", min_value=0.0, max_value=100.0, value=60.0, step=1.0, disabled=not enable_score)

    r2c1, r2c2 = st.columns([1, 2])
    enable_amp = r2c1.checkbox("历史平均振幅", value=True)
    amp_range = r2c2.slider("历史平均振幅区间（%）", 0.0, 15.0, (2.0, 8.0), 0.1, disabled=not enable_amp)

    r3c1, r3c2 = st.columns([1, 2])
    enable_amount = r3c1.checkbox("历史中位成交额", value=True)
    min_median_amount_yi = r3c2.number_input("历史中位成交额下限（亿）", min_value=0.0, value=5.0, step=1.0, disabled=not enable_amount)

    r4c1, r4c2 = st.columns([1, 2])
    enable_space = r4c1.checkbox("历史日内空间", value=True)
    intraday_space_range = r4c2.slider("历史日内空间区间（%）", 0.0, 15.0, (1.5, 8.0), 0.1, disabled=not enable_space)

    r5c1, r5c2 = st.columns([1, 2])
    enable_drawdown = r5c1.checkbox("最大回撤", value=True)
    max_drawdown_floor = r5c2.number_input("允许的最大回撤下限（%）", min_value=-100.0, max_value=0.0, value=-35.0, step=1.0, disabled=not enable_drawdown, help="-35 表示只保留最大回撤不差于 -35% 的股票。")

    r6c1, r6c2 = st.columns([1, 2])
    enable_liquidity = r6c1.checkbox("流动性得分", value=False)
    min_liquidity_score = r6c2.number_input("最低流动性得分", 0.0, 100.0, 50.0, 1.0, disabled=not enable_liquidity)

    r7c1, r7c2 = st.columns([1, 2])
    enable_reversion = r7c1.checkbox("均值回归得分", value=False)
    min_reversion_score = r7c2.number_input("最低均值回归得分", 0.0, 100.0, 45.0, 1.0, disabled=not enable_reversion)

    r8c1, r8c2 = st.columns([1, 2])
    enable_risk = r8c1.checkbox("风险控制得分", value=True)
    min_risk_score = r8c2.number_input("最低风险控制得分", 0.0, 100.0, 40.0, 1.0, disabled=not enable_risk)

    r9c1, r9c2 = st.columns([1, 2])
    enable_observations = r9c1.checkbox("有效历史样本数", value=True)
    min_observations = r9c2.number_input("最少有效交易日", 20, 120, 40, 5, disabled=not enable_observations)

spot_config = SpotFilterConfig(
    markets=tuple(selected_markets),
    exclude_st=bool(exclude_st),
    min_amount_yi=float(min_spot_amount_yi) if enable_spot_amount else 0.0,
    min_turnover=float(min_turnover) if enable_turnover else 0.0,
    min_amplitude=float(spot_amp_range[0]) if enable_spot_amp else 0.0,
    max_amplitude=float(spot_amp_range[1]) if enable_spot_amp else 100.0,
    min_pct_change=float(pct_range[0]) if enable_pct else -100.0,
    max_pct_change=float(pct_range[1]) if enable_pct else 100.0,
    min_price=float(price_range[0]) if enable_price else 0.0,
    max_price=float(price_range[1]) if enable_price else 100000.0,
)

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
    st.caption("历史主动筛选：" + " · ".join(active_descriptions))

if st.button("开始全市场做T扫描", type="primary", use_container_width=True):
    try:
        if not selected_markets:
            raise ValueError("至少选择一个市场。")
        price_provider, store = provider_and_store()
        identity_provider = universe_provider()
        status = st.status("正在执行全市场扫描...", expanded=True)

        status.write("读取独立证券股票池...")
        full_universe = select_universe(identity_provider, limit=None, exclude_st=False, min_spot_amount=0.0)
        if full_universe.empty:
            raise RuntimeError("当前证券身份链没有返回股票池。")
        full_universe["code"] = full_universe["code"].astype(str).str.zfill(6)
        available_markets = set(full_universe["market"].astype(str)) if "market" in full_universe.columns else set()
        requested_markets = set(selected_markets)
        missing_markets = requested_markets - available_markets
        if missing_markets:
            status.write("警告：当前云端身份源未覆盖 " + ", ".join(sorted(missing_markets)) + "；本轮只扫描实际可用市场。")
        scoped_universe = full_universe[full_universe["market"].astype(str).isin(requested_markets)].copy()
        if exclude_st and "name" in scoped_universe.columns:
            scoped_universe = scoped_universe[~scoped_universe["name"].astype(str).str.upper().str.contains("ST", na=False)]
        if scoped_universe.empty:
            raise RuntimeError("所选市场在当前云端股票池中没有可扫描股票。")

        market_counts = scoped_universe["market"].astype(str).value_counts().to_dict() if "market" in scoped_universe.columns else {}
        total_universe = len(scoped_universe)
        status.write(f"可用股票母集：{total_universe}只；批量获取实时筛选指标...")

        spot_provider = TencentSpotProvider(batch_size=80)
        quotes = spot_provider.quotes(scoped_universe["code"].tolist())
        quote_report = spot_provider.last_report
        status.write(f"实时行情覆盖：{quote_report.returned}/{quote_report.requested}；执行主动参数筛选...")
        first_stage = apply_spot_filters(scoped_universe, quotes, spot_config)
        if first_stage.empty:
            raise RuntimeError("第一阶段参数组合没有筛出股票，请放宽实时筛选条件。")
        candidates = first_stage.head(int(deep_limit)).copy()
        codes = candidates["code"].astype(str).tolist()
        status.write(f"第一阶段通过 {len(first_stage)}只；按流动性排序后取前 {len(codes)}只进入历史T评分。")

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
                status.write(f"注意：{hydration.failed_ranges}个历史区间补齐失败；评分继续使用可用缓存/备用源。")

        status.write("计算历史T Score...")
        scores = scan_codes(codes, price_provider, lookback=int(lookback), store=store)
        ranking = enrich_t_ranking(scores)
        if ranking.empty:
            raise RuntimeError("没有生成有效T Score结果。")
        valid = ranking[(ranking["score"] > 0) & (ranking["suitability"] != "数据异常")].copy()
        valid = valid.sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)

        st.session_state["full_market_valid_ranking"] = valid
        st.session_state["full_market_scan_meta"] = {
            "total_universe": total_universe,
            "full_identity_count": len(full_universe),
            "available_markets": sorted(available_markets),
            "missing_markets": sorted(missing_markets),
            "market_counts": market_counts,
            "quote_requested": quote_report.requested,
            "quote_returned": quote_report.returned,
            "quote_failed_batches": quote_report.failed_batches,
            "first_stage_count": len(first_stage),
            "deep_count": len(codes),
        }
        st.session_state["first_stage_preview"] = first_stage.head(200).copy()
        status.update(label="全市场扫描完成，可继续调整历史筛选参数", state="complete", expanded=False)
    except Exception as exc:
        st.error(str(exc))
        st.caption("云端股票身份、实时筛选指标、历史K线现在是三条独立数据链。某一条失败时会直接说明是哪一层，不再统一报 All stock-list providers failed。")

if "full_market_valid_ranking" in st.session_state:
    valid = st.session_state["full_market_valid_ranking"].copy()
    meta = st.session_state.get("full_market_scan_meta", {})
    filtered = apply_active_filters(valid, active_filters)
    filtered = filtered.sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)
    filtered["filter_rank"] = range(1, len(filtered) + 1)
    display_top = filtered.head(int(final_top)).copy()

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("可用股票母集", f"{int(meta.get('total_universe', 0))}只")
    m2.metric("实时参数通过", f"{int(meta.get('first_stage_count', 0))}只")
    m3.metric("进入历史评分", f"{int(meta.get('deep_count', 0))}只")
    m4.metric("历史参数通过", f"{len(filtered)}只")
    m5.metric("最终候选", f"{len(display_top)}只")

    coverage = f"实时行情覆盖 {int(meta.get('quote_returned', 0))}/{int(meta.get('quote_requested', 0))}"
    if meta.get("quote_failed_batches"):
        coverage += f"，失败批次 {meta['quote_failed_batches']}"
    st.caption(coverage)
    if meta.get("market_counts"):
        st.caption("市场构成：" + " · ".join(f"{k} {v}只" for k, v in sorted(meta["market_counts"].items())))
    if meta.get("missing_markets"):
        st.warning("当前云端证券身份源未覆盖：" + ", ".join(meta["missing_markets"]) + "。因此本轮结果不能标记为完整三市场全A股。")

    if active_descriptions:
        st.info("当前历史参数方案：" + "；".join(active_descriptions))

    with st.expander("查看第一阶段实时筛选样本", expanded=False):
        preview = st.session_state.get("first_stage_preview", pd.DataFrame()).copy()
        if not preview.empty:
            preview["amount_yi"] = pd.to_numeric(preview["amount"], errors="coerce") / 1e8
            cols = [c for c in ["code", "name", "market", "price", "pct_change", "amplitude", "turnover", "amount_yi"] if c in preview.columns]
            st.dataframe(preview[cols].rename(columns={"code":"代码","name":"名称","market":"市场","price":"现价","pct_change":"涨跌幅%","amplitude":"当日振幅%","turnover":"换手率%","amount_yi":"成交额(亿)"}), use_container_width=True, hide_index=True)

    if display_top.empty:
        st.warning("当前历史参数组合没有筛出候选。放宽第二阶段阈值会立即重新过滤，不需要重新下载历史行情。")
    else:
        top = display_top.iloc[0]
        a, b, c, d = st.columns(4)
        a.metric("当前第一候选", f"{top.get('name', '')} {top['code']}".strip())
        b.metric("T Score", f"{float(top['score']):.1f}")
        c.metric("适合度", str(top["suitability"]))
        d.metric("风险", str(top["risk_label"]))
        st.info(f"第一候选原因：{top['reason']}")

        display_top["median_amount_yi"] = pd.to_numeric(display_top["median_amount"], errors="coerce").fillna(0) / 1e8
        table = display_top[["filter_rank", "rank", "code", "name", "score", "grade", "suitability", "risk_label", "avg_amplitude", "avg_intraday_space", "median_amount_yi", "max_drawdown", "liquidity_score", "mean_reversion_score", "risk_score", "observations", "reason", "provider"]].rename(columns={
            "filter_rank":"筛选后排名", "rank":"原T排名", "code":"代码", "name":"名称", "score":"T Score", "grade":"等级", "suitability":"适合度", "risk_label":"风险", "avg_amplitude":"历史平均振幅%", "avg_intraday_space":"历史日内空间%", "median_amount_yi":"历史中位成交额(亿)", "max_drawdown":"最大回撤%", "liquidity_score":"流动性得分", "mean_reversion_score":"均值回归得分", "risk_score":"风险控制得分", "observations":"有效样本日", "reason":"为什么", "provider":"历史数据源"
        })
        st.dataframe(table, use_container_width=True, hide_index=True)
        st.download_button("下载当前参数筛选结果 CSV", filtered.to_csv(index=False, encoding="utf-8-sig"), "full_market_t_candidates_filtered.csv", "text/csv", use_container_width=True)

st.divider()
st.markdown("### 参数编辑器逻辑")
st.caption("第一阶段参数依赖当前批量行情，修改后需要重新点击扫描；第二阶段参数只过滤已经算好的历史T评分，修改后即时生效。")
st.caption("本工具用于历史行情研究和候选筛选，不构成投资建议；T Score不是涨跌预测。")
