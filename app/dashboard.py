from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import streamlit as st

from app.cli import make_provider, make_raw_provider
from data.cached_provider import CachedProvider
from data.hydrator import hydrate_codes
from data.validator import validate_ohlcv
from backtest.t_engine import CostModel, best_single_t_envelope, mean_reversion_backtest, summarize_trades
from features.daily import daily_features
from features.intraday import intraday_opportunity_features
from providers.health import check_provider_health
from scanner.market_scanner import scan_codes, select_universe
from scoring.t_score import build_t_score
from storage.duckdb_store import DuckDBStore

st.set_page_config(page_title="A股做T分析器", layout="wide")
st.title("A股做T历史分析器 · v0.2")
st.caption("量化历史日内交易空间；日线评分使用前复权，做T成本与分钟回测使用真实历史名义价格（不复权）。")

provider_choice = st.sidebar.selectbox("数据源", ["自动降级", "东方财富直连", "AKShare", "Tushare（需Token）", "离线演示"])
lookback = st.sidebar.slider("日线回看交易日", 20, 120, 60, 10)
use_cache = st.sidebar.checkbox("启用 DuckDB 本地缓存", value=True)
db_path = st.sidebar.text_input("数据库", "market.duckdb", disabled=not use_cache)
retries = st.sidebar.slider("失败重试次数", 0, 4, 2)


def provider_key() -> str:
    return {
        "离线演示": "demo",
        "东方财富直连": "eastmoney",
        "AKShare": "akshare",
        "Tushare（需Token）": "tushare",
        "自动降级": "auto",
    }[provider_choice]


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


if st.sidebar.button("检查数据源健康"):
    with st.sidebar:
        with st.spinner("检查中..."):
            health = check_provider_health(retry_provider_from_ui())
        if health.ok:
            st.success(f"正常 · {health.latency_ms:.0f} ms · 股票池 {health.universe_rows} · K线 {health.history_rows}")
        else:
            st.error(f"异常 · {health.error}")


tab1, tab2, tab3, tab4 = st.tabs(["自选池扫描", "全市场扫描", "单股深度分析", "T回测实验室"])

with tab1:
    default_codes = Path("config/watchlist.txt").read_text(encoding="utf-8") if Path("config/watchlist.txt").exists() else "300059\n601899\n601138"
    codes_text = st.text_area("股票代码（每行一个）", default_codes, height=180)
    if st.button("开始扫描自选池", type="primary"):
        codes = [x.strip() for x in codes_text.splitlines() if x.strip()]
        with st.spinner("正在读取历史行情并计算T评分..."):
            try:
                provider, store = provider_and_store()
                scores = scan_codes(codes, provider, lookback=lookback, store=store)
            except Exception as exc:
                st.error(str(exc))
                scores = pd.DataFrame()
        if not scores.empty:
            show_cols = ["code", "name", "score", "grade", "avg_amplitude", "avg_intraday_space", "median_amount", "max_drawdown", "provider", "error"]
            st.dataframe(scores[show_cols], use_container_width=True, hide_index=True)
            st.download_button("下载评分CSV", scores.to_csv(index=False, encoding="utf-8-sig"), "t_scores.csv", "text/csv")

with tab2:
    st.info("首次扫描会并发建立日K缓存，但总请求速率受控；再次运行只补缺失日期。建议先用 50~200 只验证数据源稳定性。")
    limit = st.number_input("本次候选数量", min_value=10, max_value=6000, value=50, step=10)
    min_amount_yi = st.number_input("当前成交额预筛（亿元，0=不筛）", min_value=0.0, value=5.0, step=1.0)
    exclude_st = st.checkbox("排除 ST", value=True)
    refresh_universe = st.checkbox("强制刷新全A股股票池", value=False)
    workers = st.slider("首次灌库并发数", 1, 12, 4)
    hydrate_rps = st.slider("总请求速率（次/秒）", 1.0, 10.0, 4.0, 0.5)
    if st.button("扫描全市场候选"):
        with st.spinner("读取股票池、补齐历史缓存并计算评分..."):
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
                hydration = None
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
                        requests_per_second=hydrate_rps,
                        retries=retries,
                    )
                scores = scan_codes(codes, provider, lookback=lookback, store=store)
            except Exception as exc:
                st.error(str(exc))
                scores = pd.DataFrame()
                hydration = None
        if hydration is not None:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("需补区间", hydration.requested_ranges)
            c2.metric("补齐成功", hydration.succeeded_ranges)
            c3.metric("失败", hydration.failed_ranges)
            c4.metric("新增K线", hydration.fetched_rows)
            if hydration.failures:
                st.warning("部分股票补齐失败，扫描会尝试已有缓存或正常 provider 路径。")
                rows = [{"code": x.code, "start": x.start, "end": x.end, "error": x.error} for x in hydration.failures[:20]]
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
        if not scores.empty:
            show_cols = ["code", "name", "score", "grade", "avg_amplitude", "avg_intraday_space", "median_amount", "max_drawdown", "provider", "error"]
            st.dataframe(scores[show_cols], use_container_width=True, hide_index=True)

with tab3:
    code = st.text_input("股票代码", "300059", max_chars=6)
    days = st.slider("分钟数据观察自然日", 5, 60, 20)
    if st.button("分析这只股票"):
        provider, _ = provider_and_store()
        end = date.today()
        daily_start = end - timedelta(days=200)
        minute_start = end - timedelta(days=days)
        try:
            daily = validate_ohlcv(provider.history(code, daily_start, end, interval="1d", adjust="qfq"))
            name = daily.attrs.get("name", "")
            f = daily_features(daily, lookback=lookback)
            score = build_t_score(code, name, f, daily.attrs.get("provider", provider.name))
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("T评分", score.score)
            c2.metric("等级", score.grade)
            c3.metric("平均振幅", f"{score.avg_amplitude:.2f}%")
            c4.metric("最大回撤", f"{score.max_drawdown:.2f}%")
            st.line_chart(daily.set_index("datetime")[["close"]])
            st.write("评分拆解")
            st.dataframe(pd.DataFrame([score.to_dict()]), use_container_width=True, hide_index=True)

            try:
                intraday = validate_ohlcv(provider.history(code, minute_start, end, interval="5m", adjust="none"))
                intra = intraday_opportunity_features(intraday, threshold_pct=1.0)
                st.subheader("5分钟历史T机会（不复权名义价格）")
                st.json(intra)
            except Exception as minute_exc:
                st.info(f"分钟数据当前不可用：{minute_exc}")
        except Exception as exc:
            st.error(str(exc))


with tab4:
    st.warning("“历史最佳T空间”使用全天数据挑选最佳买卖顺序，只用于衡量机会天花板，不能当作可执行策略。交易成本、最低佣金和股数约束均按不复权5分钟历史名义价格计算；滚动Z分数基线只使用当时及以前数据，并延后一根5分钟K执行。")
    bt_code = st.text_input("回测股票代码", "300059", max_chars=6, key="bt_code")
    bt_days = st.slider("回测自然日", 10, 180, 60, 10)
    bt_mode_ui = st.radio("T方向", ["正T（先买后卖旧底仓）", "倒T（先卖旧底仓后买回）"], horizontal=True)
    bt_mode = "positive" if bt_mode_ui.startswith("正T") else "reverse"
    c1, c2, c3 = st.columns(3)
    bottom_shares = c1.number_input("开盘前底仓（股）", min_value=100, value=1000, step=100)
    t_ratio = c2.slider("单次T仓占底仓比例", 0.1, 1.0, 0.5, 0.1)
    window = c3.slider("滚动窗口（5分钟K）", 3, 20, 6)
    c4, c5, c6, c7 = st.columns(4)
    commission_pct = c4.number_input("券商佣金（%）", min_value=0.0, value=0.03, step=0.005, format="%.3f")
    min_commission = c5.number_input("最低佣金（元/边）", min_value=0.0, value=5.0, step=1.0)
    stamp_pct = c6.number_input("卖出印花税（%）", min_value=0.0, value=0.05, step=0.01, format="%.3f")
    slippage_bps = c7.number_input("单边滑点（bp）", min_value=0.0, value=2.0, step=0.5)
    c8, c9 = st.columns(2)
    other_pct = c8.number_input("其他单边费率（%）", min_value=0.0, value=0.0, step=0.001, format="%.4f")
    entry_z = c9.slider("入场Z阈值", 0.5, 3.0, 1.0, 0.1)

    if st.button("运行T回测", type="primary"):
        provider, _ = provider_and_store()
        bt_end = date.today()
        costs = CostModel(
            commission_rate=float(commission_pct) / 100.0,
            min_commission=float(min_commission),
            stamp_duty_sell_rate=float(stamp_pct) / 100.0,
            other_rate_per_side=float(other_pct) / 100.0,
            slippage_bps=float(slippage_bps),
        )
        try:
            bars = validate_ohlcv(provider.history(bt_code, bt_end - timedelta(days=int(bt_days)), bt_end, interval="5m", adjust="none"))
            envelope = best_single_t_envelope(bars, bt_mode, int(bottom_shares), float(t_ratio), costs)
            baseline = mean_reversion_backtest(
                bars,
                bt_mode,
                int(bottom_shares),
                float(t_ratio),
                costs,
                window=int(window),
                entry_z=float(entry_z),
            )
            env_sum = summarize_trades(envelope)
            base_sum = summarize_trades(baseline)
            st.subheader("因果基线结果")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("交易天数", base_sum.trades)
            m2.metric("胜率", f"{base_sum.win_rate:.2f}%")
            m3.metric("累计净收益额", f"{base_sum.total_net_pnl:.2f} 元")
            m4.metric("平均单次净收益率", f"{base_sum.avg_net_return_pct:.3f}%")
            if not baseline.empty:
                st.dataframe(baseline.sort_values("date", ascending=False), use_container_width=True, hide_index=True)
            st.subheader("历史机会天花板（后视，不可直接交易）")
            e1, e2, e3 = st.columns(3)
            e1.metric("有序机会天数", env_sum.trades)
            e2.metric("平均净空间", f"{env_sum.avg_net_return_pct:.3f}%")
            e3.metric("理论累计净收益额", f"{env_sum.total_net_pnl:.2f} 元")
            if not envelope.empty:
                st.dataframe(envelope.sort_values("date", ascending=False), use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.caption("仅用于历史数据研究与策略验证，不构成投资建议。公开行情接口可能变更或限流，因此数据层保持可替换。")
