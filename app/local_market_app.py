from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from presentation.ranking import enrich_t_ranking
from presentation.screening import active_filter_descriptions, apply_active_filters
from providers.local_duckdb import LocalDuckDBProvider
from providers.tencent_spot import TencentSpotProvider
from scanner.market_scanner import scan_codes
from scanner.parameter_filters import SpotFilterConfig, apply_spot_filters
from storage.market_database import MarketDatabase


st.set_page_config(page_title="全市场做T扫描器", layout="wide")
st.title("全市场做T扫描器")
st.caption("本地股票库 → 实时/本地最新行情初筛 → 本地历史T评分 → 参数筛选 → 最终候选。历史行情不再依赖扫描时联网。")
st.caption("数据库优先架构 · raw长期保存 · qfq用于评分缓存 · 公网只负责数据维护")

DB_PATH = st.sidebar.text_input("本地行情数据库", "market.duckdb", key="main_db")
lookback = st.sidebar.slider("T Score回看交易日", 20, 120, 60, 10)
quote_mode = st.sidebar.selectbox(
    "第一阶段行情来源",
    ["自动：腾讯实时优先，本地日K兜底", "仅腾讯实时", "仅本地最新日K"],
    index=0,
)

market_db = MarketDatabase(DB_PATH)
store = market_db.store
local_provider = LocalDuckDBProvider(store)
stats = market_db.stats()

s1, s2, s3, s4 = st.columns(4)
s1.metric("本地股票池", f"{stats.universe_rows}只")
s2.metric("raw日K覆盖", f"{stats.raw_daily_codes}只")
s3.metric("qfq评分覆盖", f"{stats.qfq_daily_codes}只")
s4.metric("数据库", f"{stats.file_size_mb:.1f} MB")

if stats.universe_rows == 0 or stats.qfq_daily_codes == 0:
    st.warning("本地数据库尚未准备好。先到“数据管理”更新股票池并初始化历史数据库。")
    try:
        st.page_link("pages/2_数据管理.py", label="打开数据管理", icon="🗄️")
    except Exception:
        pass

st.markdown("### 扫描流程")
f1, f2, f3, f4 = st.columns(4)
f1.info("① 本地股票池\n\n直接读取 DuckDB，不访问股票列表公网接口。")
f2.info("② 主动初筛\n\n优先腾讯实时；失败时可自动使用数据库最新 raw 日K。")
f3.info("③ 本地历史评分\n\n只从 DuckDB 读取 qfq 历史计算 T Score，不再临时下载日K。")
f4.info("④ 参数筛选\n\n继续按历史振幅、成交额、日内空间、回撤等过滤。")

st.markdown("### 扫描规模")
c1, c2 = st.columns(2)
deep_limit = c1.number_input("进入历史T评分数量", min_value=20, max_value=1500, value=200, step=20)
final_top = c2.number_input("最终展示Top", min_value=5, max_value=200, value=20, step=5)
st.caption("全市场股票池先做第一阶段筛选；只有筛选后的 Top N 才进入历史深度评分。历史数据来自本地数据库。")

with st.expander("筛选参数编辑器", expanded=True):
    st.caption("关闭某项后该指标不参与筛选。第一阶段参数需要重新扫描；第二阶段历史参数修改后即时重新过滤。")

    st.markdown("#### 第一阶段：当前/最新交易日指标")
    selected_markets = st.multiselect("市场范围", ["SH", "SZ", "BJ"], default=["SH", "SZ", "BJ"])
    exclude_st = st.checkbox("排除 ST", value=True)

    p1a, p1b = st.columns([1, 2])
    enable_spot_amount = p1a.checkbox("成交额", value=True, key="f_spot_amount")
    min_spot_amount_yi = p1b.number_input("成交额下限（亿）", min_value=0.0, value=2.0, step=1.0, disabled=not enable_spot_amount)

    p2a, p2b = st.columns([1, 2])
    enable_turnover = p2a.checkbox("换手率", value=False, key="f_turnover")
    min_turnover = p2b.number_input("最低换手率（%）", min_value=0.0, value=0.5, step=0.1, disabled=not enable_turnover)

    p3a, p3b = st.columns([1, 2])
    enable_spot_amp = p3a.checkbox("当日振幅", value=False, key="f_spot_amp")
    spot_amp_range = p3b.slider("当日振幅区间（%）", 0.0, 30.0, (1.0, 12.0), 0.1, disabled=not enable_spot_amp)

    p4a, p4b = st.columns([1, 2])
    enable_pct = p4a.checkbox("涨跌幅", value=False, key="f_pct")
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
    max_drawdown_floor = r5c2.number_input("允许的最大回撤下限（%）", min_value=-100.0, max_value=0.0, value=-35.0, step=1.0, disabled=not enable_drawdown)

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


def get_first_stage_quotes(codes: list[str]) -> tuple[pd.DataFrame, str, str]:
    if quote_mode == "仅本地最新日K":
        quotes = market_db.latest_daily_snapshot("none")
        if quotes.empty:
            quotes = market_db.latest_daily_snapshot("qfq")
        return quotes, "本地最新日K", "非实时"

    if quote_mode == "仅腾讯实时":
        quotes = TencentSpotProvider(batch_size=80).quotes(codes)
        return quotes, "腾讯实时", "实时"

    try:
        quotes = TencentSpotProvider(batch_size=80).quotes(codes)
        return quotes, "腾讯实时", "实时"
    except Exception as exc:
        quotes = market_db.latest_daily_snapshot("none")
        if quotes.empty:
            quotes = market_db.latest_daily_snapshot("qfq")
        if quotes.empty:
            raise RuntimeError(f"实时行情失败且本地日K也为空：{exc}") from exc
        return quotes, "本地最新日K", f"腾讯失败后降级：{exc}"


if st.button("开始全市场做T扫描", type="primary", width="stretch"):
    try:
        if not selected_markets:
            raise ValueError("至少选择一个市场。")

        status = st.status("正在从本地数据库执行全市场扫描...", expanded=True)
        status.write("读取本地股票池...")
        full_universe = local_provider.stock_list()
        full_universe["code"] = full_universe["code"].astype(str).str.zfill(6)
        if "market" in full_universe.columns:
            full_universe = full_universe[full_universe["market"].astype(str).str.upper().isin(selected_markets)]
        if exclude_st and "name" in full_universe.columns:
            full_universe = full_universe[~full_universe["name"].astype(str).str.upper().str.contains("ST", na=False)]
        if full_universe.empty:
            raise RuntimeError("本地股票池在当前市场条件下为空。")

        total_universe = len(full_universe)
        status.write(f"本地股票母集：{total_universe}只；获取第一阶段行情...")
        quotes, quote_source, quote_note = get_first_stage_quotes(full_universe["code"].tolist())
        if quote_source == "本地最新日K" and enable_turnover:
            raise RuntimeError("本地日K兜底没有可靠换手率字段。请关闭“换手率”筛选，或切换到“仅腾讯实时”。")

        first_stage = apply_spot_filters(full_universe, quotes, spot_config)
        if first_stage.empty:
            raise RuntimeError("第一阶段参数组合没有筛出股票，请放宽条件。")

        # Only symbols with local qfq history can enter historical scoring.
        qfq_snapshot = market_db.latest_daily_snapshot("qfq")
        qfq_codes = set(qfq_snapshot["code"].astype(str).str.zfill(6)) if not qfq_snapshot.empty else set()
        if not qfq_codes:
            raise RuntimeError("本地数据库还没有 qfq 日K。请先到“数据管理”初始化历史数据库。")
        scoreable = first_stage[first_stage["code"].astype(str).str.zfill(6).isin(qfq_codes)].copy()
        missing_history_count = len(first_stage) - len(scoreable)
        if scoreable.empty:
            raise RuntimeError("第一阶段候选在本地都没有 qfq 历史数据。请先执行增量更新/历史修复。")

        candidates = scoreable.head(int(deep_limit)).copy()
        codes = candidates["code"].astype(str).tolist()
        status.write(
            f"第一阶段通过 {len(first_stage)}只；其中本地qfq可评分 {len(scoreable)}只；"
            f"取前 {len(codes)}只进行本地T Score计算。"
        )

        scores = scan_codes(codes, local_provider, lookback=int(lookback), store=store)
        ranking = enrich_t_ranking(scores)
        valid = ranking[(pd.to_numeric(ranking["score"], errors="coerce").fillna(0) > 0) & (ranking["suitability"] != "数据异常")].copy()
        valid = valid.sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)
        if valid.empty:
            raise RuntimeError("本地历史评分没有生成有效候选，请检查 qfq 数据完整性。")

        st.session_state["full_market_valid_ranking"] = valid
        st.session_state["full_market_scan_meta"] = {
            "total_universe": total_universe,
            "first_stage_count": len(first_stage),
            "scoreable_count": len(scoreable),
            "missing_history_count": missing_history_count,
            "deep_count": len(codes),
            "valid_count": len(valid),
            "quote_source": quote_source,
            "quote_note": quote_note,
        }
        st.session_state["first_stage_preview"] = first_stage.head(200).copy()
        status.update(label="本地全市场扫描完成", state="complete", expanded=False)
    except Exception as exc:
        st.error(str(exc))
        st.caption("历史行情已经从扫描流程解耦。若提示本地数据缺失，请到“数据管理”补库，而不是反复重试扫描。")

if "full_market_valid_ranking" in st.session_state:
    valid = st.session_state["full_market_valid_ranking"].copy()
    meta = st.session_state.get("full_market_scan_meta", {})
    filtered = apply_active_filters(valid, active_filters)
    filtered = filtered.sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)
    filtered["filter_rank"] = range(1, len(filtered) + 1)
    display_top = filtered.head(int(final_top)).copy()

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("本地股票母集", f"{int(meta.get('total_universe', 0))}只")
    m2.metric("第一阶段通过", f"{int(meta.get('first_stage_count', 0))}只")
    m3.metric("本地可评分", f"{int(meta.get('scoreable_count', 0))}只")
    m4.metric("完成深度评分", f"{int(meta.get('valid_count', 0))}只")
    m5.metric("最终候选", f"{len(display_top)}只")

    quote_source = str(meta.get("quote_source", ""))
    note = str(meta.get("quote_note", ""))
    if quote_source == "腾讯实时":
        st.success("第一阶段行情：腾讯实时；历史评分：本地 DuckDB。")
    else:
        st.warning("第一阶段行情：本地最新日K（非实时）；历史评分：本地 DuckDB。")
    if note and note not in {"实时", "非实时"}:
        st.caption(note)
    if int(meta.get("missing_history_count", 0)) > 0:
        st.info(f"第一阶段有 {int(meta['missing_history_count'])} 只缺少本地 qfq 历史，已自动跳过；可到“数据管理”补齐。")

    if active_descriptions:
        st.info("当前历史参数方案：" + "；".join(active_descriptions))

    with st.expander("查看第一阶段筛选样本", expanded=False):
        preview = st.session_state.get("first_stage_preview", pd.DataFrame()).copy()
        if not preview.empty:
            if "amount" in preview.columns:
                preview["amount_yi"] = pd.to_numeric(preview["amount"], errors="coerce") / 1e8
            cols = [c for c in ["code", "name", "market", "price", "pct_change", "amplitude", "turnover", "amount_yi"] if c in preview.columns]
            st.dataframe(
                preview[cols].rename(columns={
                    "code": "代码", "name": "名称", "market": "市场", "price": "现价/最新收盘",
                    "pct_change": "涨跌幅%", "amplitude": "振幅%", "turnover": "换手率%", "amount_yi": "成交额(亿)",
                }),
                width="stretch",
                hide_index=True,
            )

    if display_top.empty:
        st.warning("当前历史参数组合没有候选。放宽第二阶段参数即可即时重新筛选，不需要重新下载数据。")
    else:
        top = display_top.iloc[0]
        a, b, c, d = st.columns(4)
        a.metric("当前第一候选", f"{top.get('name', '')} {top['code']}".strip())
        b.metric("T Score", f"{float(top['score']):.1f}")
        c.metric("适合度", str(top["suitability"]))
        d.metric("风险", str(top["risk_label"]))
        st.info(f"第一候选原因：{top['reason']}")

        display_top["median_amount_yi"] = pd.to_numeric(display_top["median_amount"], errors="coerce").fillna(0) / 1e8
        table_cols = [
            "filter_rank", "rank", "code", "name", "score", "grade", "suitability", "risk_label",
            "avg_amplitude", "avg_intraday_space", "median_amount_yi", "max_drawdown",
            "liquidity_score", "mean_reversion_score", "risk_score", "observations", "reason", "provider",
        ]
        table_cols = [c for c in table_cols if c in display_top.columns]
        table = display_top[table_cols].rename(columns={
            "filter_rank": "筛选后排名", "rank": "原T排名", "code": "代码", "name": "名称",
            "score": "T Score", "grade": "等级", "suitability": "适合度", "risk_label": "风险",
            "avg_amplitude": "历史平均振幅%", "avg_intraday_space": "历史日内空间%",
            "median_amount_yi": "历史中位成交额(亿)", "max_drawdown": "最大回撤%",
            "liquidity_score": "流动性得分", "mean_reversion_score": "均值回归得分",
            "risk_score": "风险控制得分", "observations": "有效样本日", "reason": "为什么", "provider": "历史数据源",
        })
        st.dataframe(table, width="stretch", hide_index=True)
        st.download_button(
            "下载当前参数筛选结果 CSV",
            filtered.to_csv(index=False, encoding="utf-8-sig"),
            "full_market_t_candidates_filtered.csv",
            "text/csv",
            width="stretch",
        )

        try:
            market_db.record_scan_run(
                universe_rows=int(meta.get("total_universe", 0)),
                first_stage_rows=int(meta.get("first_stage_count", 0)),
                deep_rows=int(meta.get("deep_count", 0)),
                valid_score_rows=int(meta.get("valid_count", 0)),
                final_rows=len(display_top),
                parameters=json.dumps({"spot": spot_config.__dict__, "history": active_filters}, ensure_ascii=False, default=str),
                note=f"quote_source={quote_source}",
            )
        except Exception:
            pass

st.divider()
st.caption("正常扫描不再调用 BaoStock / 东财 / AKShare 历史接口。缺数据时到“数据管理”一次性补库；以后每天只做增量更新。")
