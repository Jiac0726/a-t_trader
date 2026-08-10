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

from app.cli import make_provider
from backtest.t_engine import CostModel, best_single_t_envelope, mean_reversion_backtest, summarize_trades
from data.cached_provider import CachedProvider
from data.validator import validate_ohlcv
from features.daily import daily_features
from features.intraday import intraday_opportunity_features
from presentation.ranking import enrich_t_ranking
from presentation.single_stock_detail import build_component_detail, build_raw_metric_detail
from scoring.t_score import build_t_score
from storage.duckdb_store import DuckDBStore

st.set_page_config(page_title="综合分析与回测", layout="wide")
st.title("综合分析与回测")
st.caption("用于全市场候选出来后的单股深挖：不仅看T Score，还展开每个维度的原始值、评分区间、历史股性和5分钟T机会。")

SOURCE_MAP = {
    "自动降级（推荐）": "auto",
    "东方财富": "eastmoney",
    "AKShare": "akshare",
    "Tushare（需Token）": "tushare",
    "离线演示": "demo",
}

provider_choice = st.sidebar.selectbox("历史行情数据源", list(SOURCE_MAP), index=0, key="analysis_provider")
lookback = st.sidebar.slider("T Score回看交易日", 20, 120, 60, 10, key="analysis_lookback")
use_cache = st.sidebar.checkbox("启用 DuckDB 缓存", value=True, key="analysis_cache")
db_path = st.sidebar.text_input("缓存数据库", "market.duckdb", disabled=not use_cache, key="analysis_db")
retries = st.sidebar.slider("失败重试次数", 0, 4, 2, key="analysis_retries")


def provider_and_store():
    raw = make_provider(SOURCE_MAP[provider_choice], retries=int(retries))
    if not use_cache:
        return raw, None
    store = DuckDBStore(db_path)
    return CachedProvider(raw, store), store


def candidate_default_code() -> str:
    for key in ("full_market_valid_ranking", "latest_ranking"):
        frame = st.session_state.get(key)
        if isinstance(frame, pd.DataFrame) and not frame.empty and "code" in frame.columns:
            return str(frame.iloc[0]["code"]).zfill(6)
    return "300059"


tab_stock, tab_backtest = st.tabs(["单股分析", "正T / 倒T回测"])

with tab_stock:
    st.subheader("单股历史股性")
    code = st.text_input("股票代码", candidate_default_code(), max_chars=6, key="analysis_code")
    days = st.slider("5分钟观察自然日", 5, 60, 20, key="analysis_days")

    if st.button("分析这只股票", type="primary", key="analysis_run"):
        provider, _ = provider_and_store()
        end = date.today()
        try:
            daily = validate_ohlcv(
                provider.history(code, end - timedelta(days=220), end, interval="1d", adjust="qfq")
            )
            name = daily.attrs.get("name", "")
            features = daily_features(daily, lookback=int(lookback))
            score = build_t_score(code, name, features, daily.attrs.get("provider", provider.name))
            interpreted = enrich_t_ranking(pd.DataFrame([score.to_dict()])).iloc[0]

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("T Score", f"{score.score:.1f}")
            c2.metric("适合度", str(interpreted["suitability"]))
            c3.metric("风险", str(interpreted["risk_label"]))
            c4.metric("平均振幅", f"{score.avg_amplitude:.2f}%")
            c5.metric("中位成交额", f"{score.median_amount / 1e8:.2f}亿")
            st.info(str(interpreted["reason"]))

            st.markdown("#### T Score 分项：得分 + 原始值 + 评分区间")
            st.caption("分数只用于排序；真正判断一只股票是否适合做T，应同时看原始指标处在什么区间。")
            components = build_component_detail(score, features)
            st.dataframe(
                components,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "分数": st.column_config.NumberColumn("分数", format="%.2f"),
                    "原始值": st.column_config.TextColumn("原始值", width="medium"),
                    "评分参考": st.column_config.TextColumn("评分参考", width="large"),
                    "当前状态": st.column_config.TextColumn("当前状态", width="medium"),
                },
            )

            with st.expander("查看全部原始指标明细", expanded=True):
                raw_detail = build_raw_metric_detail(features)
                st.dataframe(
                    raw_detail,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "原始值": st.column_config.NumberColumn("原始值", format="%.3f"),
                        "参考/说明": st.column_config.TextColumn("参考/说明", width="large"),
                    },
                )
                st.caption(
                    "注意：ATR(14)/收盘价目前作为辅助观察指标，并未直接进入T Score；其余标注为评分维度的指标均来自当前实际评分代码。"
                )

            st.markdown("#### 日线走势")
            st.line_chart(daily.set_index("datetime")[["close"]])

            try:
                intraday = validate_ohlcv(
                    provider.history(code, end - timedelta(days=int(days)), end, interval="5m", adjust="none")
                )
                intra = intraday_opportunity_features(intraday, threshold_pct=1.0)
                st.markdown("#### 5分钟历史T机会")
                st.caption("分钟分析使用不复权历史名义价格，仅描述历史股性。")
                st.json(intra)
            except Exception as minute_exc:
                st.warning(f"分钟数据当前不可用：{minute_exc}")
        except Exception as exc:
            st.error(str(exc))

with tab_backtest:
    st.subheader("正T / 倒T历史对比")
    st.caption("遵守A股T+1约束：正T买入后卖出等量旧底仓；倒T先卖旧底仓再买回。")
    st.warning("历史最佳空间使用全天后视信息，只用于比较历史可实现空间；因果基线才按当时信息生成信号。")

    bt_code = st.text_input("回测股票代码", candidate_default_code(), max_chars=6, key="bt_code")
    bt_days = st.slider("回测自然日", 10, 180, 60, 10, key="bt_days")

    c1, c2, c3 = st.columns(3)
    bottom_shares = c1.number_input("开盘前底仓（股）", min_value=100, value=1000, step=100, key="bt_bottom")
    t_ratio = c2.slider("单次T仓占底仓比例", 0.1, 1.0, 0.5, 0.1, key="bt_ratio")
    window = c3.slider("滚动窗口（5分钟K）", 3, 20, 6, key="bt_window")

    c4, c5, c6, c7 = st.columns(4)
    commission_pct = c4.number_input("券商佣金（%）", min_value=0.0, value=0.03, step=0.005, format="%.3f", key="bt_commission")
    min_commission = c5.number_input("最低佣金（元/边）", min_value=0.0, value=5.0, step=1.0, key="bt_min_commission")
    stamp_pct = c6.number_input("卖出印花税（%）", min_value=0.0, value=0.05, step=0.01, format="%.3f", key="bt_stamp")
    slippage_bps = c7.number_input("单边滑点（bp）", min_value=0.0, value=2.0, step=0.5, key="bt_slippage")
    entry_z = st.slider("因果基线入场Z阈值", 0.5, 3.0, 1.0, 0.1, key="bt_entry_z")

    if st.button("比较正T / 倒T", type="primary", key="bt_run"):
        provider, _ = provider_and_store()
        end = date.today()
        costs = CostModel(
            commission_rate=float(commission_pct) / 100.0,
            min_commission=float(min_commission),
            stamp_duty_sell_rate=float(stamp_pct) / 100.0,
            slippage_bps=float(slippage_bps),
        )
        try:
            bars = validate_ohlcv(
                provider.history(bt_code, end - timedelta(days=int(bt_days)), end, interval="5m", adjust="none")
            )
            positive_env = best_single_t_envelope(bars, "positive", int(bottom_shares), float(t_ratio), costs)
            reverse_env = best_single_t_envelope(bars, "reverse", int(bottom_shares), float(t_ratio), costs)
            positive_sum = summarize_trades(positive_env)
            reverse_sum = summarize_trades(reverse_env)

            p1, p2, p3, p4 = st.columns(4)
            p1.metric("正T历史平均净空间", f"{positive_sum.avg_net_return_pct:.3f}%")
            p2.metric("正T机会天数", positive_sum.trades)
            p3.metric("倒T历史平均净空间", f"{reverse_sum.avg_net_return_pct:.3f}%")
            p4.metric("倒T机会天数", reverse_sum.trades)

            preferred_mode = "positive" if positive_sum.avg_net_return_pct >= reverse_sum.avg_net_return_pct else "reverse"
            preferred_cn = "正T" if preferred_mode == "positive" else "倒T"
            st.info(f"历史后视空间对比：{preferred_cn} 更占优。该结论只描述历史空间，不预测下一交易日方向。")

            baseline = mean_reversion_backtest(
                bars,
                preferred_mode,
                int(bottom_shares),
                float(t_ratio),
                costs,
                window=int(window),
                entry_z=float(entry_z),
            )
            base_sum = summarize_trades(baseline)
            st.markdown(f"#### {preferred_cn} 因果基线")
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("交易天数", base_sum.trades)
            b2.metric("胜率", f"{base_sum.win_rate:.2f}%")
            b3.metric("累计净收益额", f"{base_sum.total_net_pnl:.2f} 元")
            b4.metric("平均单次净收益率", f"{base_sum.avg_net_return_pct:.3f}%")
            if not baseline.empty:
                st.dataframe(baseline.sort_values("date", ascending=False), use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.caption("本工具用于历史行情研究和候选筛选，不构成投资建议。")
