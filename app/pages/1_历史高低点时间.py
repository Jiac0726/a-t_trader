from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from data.cached_provider import CachedProvider
from data.validator import validate_ohlcv
from features.time_profile import extreme_time_distribution, summarize_extreme_times
from providers.akshare_provider import AkshareProvider
from providers.chain import ProviderChain
from providers.demo import DemoProvider
from providers.eastmoney import EastmoneyProvider
from providers.retrying import RetryingProvider
from storage.duckdb_store import DuckDBStore

st.set_page_config(page_title="历史高低点时间", layout="wide")
st.title("历史日内高低点时间分布")
st.caption("统计5分钟K中每日最高/最低价首次出现时间，用来描述历史股性，不预测未来固定时点。")

provider_choice = st.sidebar.selectbox("数据源", ["自动降级", "东方财富直连", "AKShare", "离线演示"])
use_cache = st.sidebar.checkbox("启用 DuckDB 缓存", value=True)
db_path = st.sidebar.text_input("数据库", "market.duckdb", disabled=not use_cache)
retries = st.sidebar.slider("失败重试次数", 0, 4, 2)


def make_provider():
    attempts = retries + 1
    if provider_choice == "离线演示":
        raw = DemoProvider()
    elif provider_choice == "东方财富直连":
        raw = RetryingProvider(EastmoneyProvider(), attempts=attempts)
    elif provider_choice == "AKShare":
        raw = RetryingProvider(AkshareProvider(), attempts=attempts)
    else:
        raw = ProviderChain([
            RetryingProvider(EastmoneyProvider(), attempts=attempts),
            RetryingProvider(AkshareProvider(), attempts=attempts),
        ])
    if not use_cache:
        return raw
    return CachedProvider(raw, DuckDBStore(db_path))


code = st.text_input("股票代码", "300059", max_chars=6)
days = st.slider("观察自然日", 10, 180, 60, 10)
bucket = st.select_slider("时间桶", options=[5, 10, 15, 30, 60], value=30, format_func=lambda x: f"{x}分钟")

if st.button("开始统计", type="primary"):
    end = date.today()
    try:
        bars = validate_ohlcv(
            make_provider().history(code, end - timedelta(days=days), end, interval="5m", adjust="qfq")
        )
        summary = summarize_extreme_times(bars)
        dist = extreme_time_distribution(bars, bucket_minutes=int(bucket))

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("样本交易日", summary.days)
        c2.metric("最低点中位时间", summary.median_low_time or "-")
        c3.metric("最高点中位时间", summary.median_high_time or "-")
        c4.metric("尾盘最低点占比", f"{summary.last_hour_low_rate:.1f}%")
        c5.metric("尾盘最高点占比", f"{summary.last_hour_high_rate:.1f}%")

        if not dist.empty:
            pivot = dist.pivot(index="bucket", columns="kind", values="pct").fillna(0)
            pivot = pivot.rename(columns={"low": "最低点占比%", "high": "最高点占比%"})
            st.bar_chart(pivot)
            st.dataframe(dist, use_container_width=True, hide_index=True)

        st.info("说明：每个交易日取5分钟K中最高价/最低价首次出现的那根K线；这是历史统计画像，不代表未来会在相同时间形成高低点。")
    except Exception as exc:
        st.error(str(exc))
