from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from app.cli import make_provider, make_raw_provider
from backtest.t_engine import CostModel, best_single_t_envelope, mean_reversion_backtest, summarize_trades
from data.cached_provider import CachedProvider
from data.hydrator import hydrate_codes
from data.validator import validate_ohlcv
from features.daily import daily_features
from features.intraday import intraday_opportunity_features
from presentation.ranking import enrich_t_ranking
from providers.health import check_provider_health
from scanner.market_scanner import scan_codes, select_universe
from scoring.t_score import build_t_score
from storage.duckdb_store import DuckDBStore


st.set_page_config(page_title="A股做T候选分析器", layout="wide")
st.title("A股做T候选分析器")
st.caption("先回答『哪些股票更适合做T、为什么、风险多大』。日线评分使用前复权；交易成本与分钟回测使用不复权历史名义价格。")

SOURCE_MAP = {
    "自动降级": "auto",
    "东方财富": "eastmoney",
    "AKShare": "akshare",
    "Tushare（需Token）": "tushare",
    "离线演示": "demo",
}

provider_choice = st.sidebar.selectbox("数据源", list(SOURCE_MAP))
lookback = st.sidebar.slider("评分回看交易日", 20, 120, 60, 10)
use_cache = st.sidebar.checkbox("启用 DuckDB 缓存", value=True)
db_path = st.sidebar.text_input("数据库", "market.duckdb", disabled=not use_cache)
retries = st.sidebar.slider("失败重试次数", 0, 4, 2)


def provider_key() -> str:
    return SOURCE_MAP[provider_choice]


def raw_provider_from_ui():
    return make_raw_provider(provider_key())


def retry_provider_from_ui():
    return make_provider(provider_key(), retries=retries)


def provider_and_store():
    raw = retry_provider_from_ui()
    if not use_cache:
        return raw, None
    store = DuckDBStore(db_path)
    return CachedProvider(raw, store), store


def ranking_view(scores: pd.DataFrame) -> pd.DataFrame:
    ranking = enrich_t_ranking(scores)
    if ranking.empty:
        return ranking
    ranking = ranking.copy()
    ranking["median_amount_yi"] = pd.to_numeric(ranking["median_amount"], errors="coerce").fillna(0) / 1e8
    return ranking


def show_ranking(scores: pd.DataFrame) -> None:
    ranking = ranking_view(scores)
    if ranking.empty:
        st.warning("没有得到可展示的评分结果。")
        return

    st.session_state["latest_ranking"] = ranking
    valid = ranking[ranking["suitability"] != "数据异常"]
    top = valid.iloc[0] if not valid.empty else ranking.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("当前第一候选", f"{top.get('name', '')} {top['code']}".strip())
    c2.metric("T Score", f"{float(top['score']):.1f}")
    c3.metric("适合度", str(top["suitability"]))
    c4.metric("风险", str(top["risk_label"]))
    st.info(f"第一候选原因：{top['reason']}")

    display = ranking[
        [
            "rank", "code", "name", "score", "grade", "suitability", "risk_label",
            "avg_amplitude", "avg_intraday_space", "median_amount_yi", "reason", "provider",
        ]
    ].rename(
        columns={
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
        }
    )
    st.dataframe(display, use_container_width=True, hide_index=True)
    st.download_button(
        "下载候选榜 CSV",
        ranking.to_csv(index=False, encoding="utf-8-sig"),
        "t_candidate_ranking.csv",
        "text/csv",
    )


if st.sidebar.button("检查数据源"):
    with st.sidebar:
        with st.spinner("检查中..."):
            health = check_provider_health(retry_provider_from_ui())
        if health.ok:
            st.success(f"正常 · {health.latency_ms:.0f} ms · 股票池 {health.universe_rows} · K线 {health.history_rows}")
        else:
            st.error(f"异常 · {health.error}")


tab_rank, tab_stock, tab_backtest = st.tabs(["做T候选榜", "单股分析", "正T / 倒T回测"])

with tab_rank:
    st.subheader("做T候选榜")
    scope = st.radio("筛选范围", ["自选池", "全市场候选"], horizontal=True)

    if scope == "自选池":
        default_codes = (
            Path("config/watchlist.txt").read_text(encoding="utf-8")
            if Path("config/watchlist.txt").exists()
            else "300059\n601899\n601138\n000063"
        )
        codes_text = st.text_area("股票代码（每行一个）", default_codes, height=150)
        if st.button("生成自选池候选榜", type="primary"):
            codes = [x.strip() for x in codes_text.splitlines() if x.strip()]
            with st.spinner("读取历史行情并计算候选榜..."):
                try:
                    provider, store = provider_and_store()
                    scores = scan_codes(codes, provider, lookback=lookback, store=store)
                    show_ranking(scores)
                except Exception as exc:
                    st.error(str(exc))
    else:
        st.caption("全市场模式先做日线粗筛，不会给5000多只股票全部下载分钟数据。建议先扫描100~300只高流动性候选。")
        c1, c2, c3 = st.columns(3)
        limit = c1.number_input("本轮最多扫描", min_value=20, max_value=6000, value=100, step=20)
        min_amount_yi = c2.number_input("实时成交额预筛(亿，0=关闭)", min_value=0.0, value=0.0, step=1.0)
        workers = c3.slider("日K灌库并发", 1, 12, 4)
        c4, c5 = st.columns(2)
        exclude_st = c4.checkbox("排除 ST", value=True)
        refresh_universe = c5.checkbox("刷新股票池", value=False)

        if st.button("生成全市场候选榜", type="primary"):
            with st.spinner("读取股票池、补齐日K并评分..."):
                try:
                    provider, store = provider_and_store()
                    if refresh_universe and isinstance(provider, CachedProvider):
                        provider.refresh_stock_list()
                    candidates = select_universe(
                        provider,
                        limit=int(limit),
                        exclude_st=exclude_st,
                        min_spot_amount=float(min_amount_yi) * 1e8,
                    )
                    codes = candidates["code"].tolist() if not candidates.empty else []
                    if codes and store is not None and workers > 1:
                        end = date.today()
                        hydration = hydrate_codes(
                            codes,
                            provider_factory=raw_provider_from_ui,
                            store=store,
                            start=end - timedelta(days=140),
                            end=end,
                            interval="1d",
                            adjust="qfq",
                            workers=workers,
                            requests_per_second=4.0,
                            retries=retries,
                        )
                        if hydration.failed_ranges:
                            st.warning(f"有 {hydration.failed_ranges} 个历史区间补齐失败；评分会继续使用可用缓存/备用数据源。")
                    scores = scan_codes(codes, provider, lookback=lookback, store=store)
                    show_ranking(scores)
                except Exception as exc:
                    st.error(str(exc))

    if "latest_ranking" in st.session_state:
        ranking = st.session_state["latest_ranking"]
        if not ranking.empty:
            st.markdown("#### 怎么看这张榜")
            st.caption("T Score 是历史适合度，不是明日涨跌预测。优先看『适合度 + 风险 + 为什么』，再进入单股分析和正T/倒T回测。")


with tab_stock:
    st.subheader("单股历史股性")
    default_code = "300059"
    if "latest_ranking" in st.session_state and not st.session_state["latest_ranking"].empty:
        default_code = str(st.session_state["latest_ranking"].iloc[0]["code"])
    code = st.text_input("股票代码", default_code, max_chars=6)
    days = st.slider("5分钟观察自然日", 5, 60, 20)

    if st.button("分析这只股票"):
        provider, _ = provider_and_store()
        end = date.today()
        try:
            daily = validate_ohlcv(provider.history(code, end - timedelta(days=200), end, interval="1d", adjust="qfq"))
            name = daily.attrs.get("name", "")
            features = daily_features(daily, lookback=lookback)
            score = build_t_score(code, name, features, daily.attrs.get("provider", provider.name))
            interpreted = enrich_t_ranking(pd.DataFrame([score.to_dict()])).iloc[0]

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("T Score", f"{score.score:.1f}")
            c2.metric("适合度", interpreted["suitability"])
            c3.metric("风险", interpreted["risk_label"])
            c4.metric("平均振幅", f"{score.avg_amplitude:.2f}%")
            st.info(interpreted["reason"])
            st.line_chart(daily.set_index("datetime")[["close"]])

            components = pd.DataFrame(
                {
                    "维度": ["振幅空间", "流动性", "日内空间", "均值回归", "趋势稳定", "风险控制"],
                    "分数": [
                        score.amplitude_score,
                        score.liquidity_score,
                        score.tradable_space_score,
                        score.mean_reversion_score,
                        score.trend_score,
                        score.risk_score,
                    ],
                }
            )
            st.dataframe(components, use_container_width=True, hide_index=True)

            try:
                intraday = validate_ohlcv(
                    provider.history(code, end - timedelta(days=days), end, interval="5m", adjust="none")
                )
                intra = intraday_opportunity_features(intraday, threshold_pct=1.0)
                st.markdown("#### 5分钟历史T机会")
                st.caption("以下使用不复权历史名义价格，只描述历史股性。")
                st.json(intra)
            except Exception as minute_exc:
                st.info(f"分钟数据当前不可用：{minute_exc}")
        except Exception as exc:
            st.error(str(exc))


with tab_backtest:
    st.subheader("正T / 倒T历史对比")
    st.warning("历史最佳空间使用了全天后视信息，只用于比较正T/倒T的历史空间；因果基线才按当时信息生成信号。所有交易成本按不复权5分钟名义价格计算。")
    bt_code = st.text_input("回测股票代码", "300059", max_chars=6, key="bt_code")
    bt_days = st.slider("回测自然日", 10, 180, 60, 10)
    c1, c2, c3 = st.columns(3)
    bottom_shares = c1.number_input("开盘前底仓（股）", min_value=100, value=1000, step=100)
    t_ratio = c2.slider("单次T仓占底仓比例", 0.1, 1.0, 0.5, 0.1)
    window = c3.slider("滚动窗口（5分钟K）", 3, 20, 6)
    c4, c5, c6, c7 = st.columns(4)
    commission_pct = c4.number_input("券商佣金（%）", min_value=0.0, value=0.03, step=0.005, format="%.3f")
    min_commission = c5.number_input("最低佣金（元/边）", min_value=0.0, value=5.0, step=1.0)
    stamp_pct = c6.number_input("卖出印花税（%）", min_value=0.0, value=0.05, step=0.01, format="%.3f")
    slippage_bps = c7.number_input("单边滑点（bp）", min_value=0.0, value=2.0, step=0.5)
    entry_z = st.slider("因果基线入场Z阈值", 0.5, 3.0, 1.0, 0.1)

    if st.button("比较正T / 倒T", type="primary"):
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
            st.info(f"历史后视空间对比：{preferred_cn} 更占优。该结论只描述历史空间，不是下一交易日方向预测。")

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
st.caption("本工具用于历史行情研究和候选筛选，不构成投资建议；T Score 不是涨跌预测，真实交易仍需考虑停牌、涨跌停、成交冲击与个人佣金口径。")
