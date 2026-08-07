from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import streamlit as st

from data.cached_provider import CachedProvider
from data.validator import validate_ohlcv
from features.daily import daily_features
from features.intraday import intraday_opportunity_features
from providers.akshare_provider import AkshareProvider
from providers.chain import ProviderChain
from providers.demo import DemoProvider
from providers.eastmoney import EastmoneyProvider
from scanner.market_scanner import scan_codes, scan_universe
from scoring.t_score import build_t_score
from storage.duckdb_store import DuckDBStore

st.set_page_config(page_title="A股做T分析器", layout="wide")
st.title("A股做T历史分析器 · v0.2")
st.caption("量化历史日内交易空间；支持股票池、DuckDB缓存和分层扫描。")

provider_choice = st.sidebar.selectbox("数据源", ["自动降级", "东方财富直连", "AKShare", "离线演示"])
lookback = st.sidebar.slider("日线回看交易日", 20, 120, 60, 10)
use_cache = st.sidebar.checkbox("启用 DuckDB 本地缓存", value=True)
db_path = st.sidebar.text_input("数据库", "market.duckdb", disabled=not use_cache)


def raw_provider_from_ui():
    if provider_choice == "离线演示":
        return DemoProvider()
    if provider_choice == "东方财富直连":
        return EastmoneyProvider()
    if provider_choice == "AKShare":
        return AkshareProvider()
    return ProviderChain([EastmoneyProvider(), AkshareProvider()])


def provider_and_store():
    raw = raw_provider_from_ui()
    if not use_cache:
        return raw, None
    store = DuckDBStore(db_path)
    return CachedProvider(raw, store), store


tab1, tab2, tab3 = st.tabs(["自选池扫描", "全市场扫描", "单股深度分析"])

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
    st.info("第一次扫描需要为候选股票建立历史缓存。建议先限制 50~200 只验证，再逐步扩大；后续运行只补增量日期。")
    limit = st.number_input("本次候选数量", min_value=10, max_value=6000, value=50, step=10)
    min_amount_yi = st.number_input("当前成交额预筛（亿元，0=不筛）", min_value=0.0, value=5.0, step=1.0)
    exclude_st = st.checkbox("排除 ST", value=True)
    refresh_universe = st.checkbox("强制刷新全A股股票池", value=False)
    if st.button("扫描全市场候选"):
        with st.spinner("读取股票池并扫描候选..."):
            try:
                provider, store = provider_and_store()
                if refresh_universe and isinstance(provider, CachedProvider):
                    provider.refresh_stock_list()
                scores = scan_universe(
                    provider,
                    limit=int(limit),
                    exclude_st=exclude_st,
                    min_spot_amount=float(min_amount_yi) * 1e8,
                    lookback=lookback,
                    store=store,
                )
            except Exception as exc:
                st.error(str(exc))
                scores = pd.DataFrame()
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
                intraday = validate_ohlcv(provider.history(code, minute_start, end, interval="5m", adjust="qfq"))
                intra = intraday_opportunity_features(intraday, threshold_pct=1.0)
                st.subheader("5分钟历史T机会")
                st.json(intra)
            except Exception as minute_exc:
                st.info(f"分钟数据当前不可用：{minute_exc}")
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.caption("仅用于历史数据研究与策略验证，不构成投资建议。公开行情接口可能变更或限流，因此数据层保持可替换。")
