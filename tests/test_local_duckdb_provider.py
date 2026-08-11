from __future__ import annotations

import pandas as pd
import pytest

from providers.base import NoMarketData
from providers.local_duckdb import LocalDuckDBProvider
from storage.duckdb_store import DuckDBStore


def _bars():
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-08-07", "2026-08-08"]),
            "open": [10.0, 10.2],
            "high": [10.5, 10.8],
            "low": [9.8, 10.1],
            "close": [10.2, 10.6],
            "volume": [1000, 1200],
            "amount": [10_000_000, 12_000_000],
        }
    )
    df.attrs["provider"] = "fixture"
    return df


def test_local_provider_reads_stock_list_and_history_without_upstream(tmp_path):
    store = DuckDBStore(tmp_path / "market.duckdb")
    store.save_stock_list(pd.DataFrame([{"code": "600000", "name": "浦发银行", "market": "SH"}]), provider="fixture")
    store.save_history("600000", "1d", _bars(), adjust="qfq")

    provider = LocalDuckDBProvider(store)
    stocks = provider.stock_list()
    assert stocks.iloc[0]["code"] == "600000"

    out = provider.history("600000", "2026-08-01", "2026-08-10", interval="1d", adjust="qfq")
    assert len(out) == 2
    assert out.attrs["provider"] == "local-duckdb"
    assert out.attrs["name"] == "浦发银行"


def test_local_provider_fails_closed_when_history_missing(tmp_path):
    store = DuckDBStore(tmp_path / "market.duckdb")
    store.save_stock_list(pd.DataFrame([{"code": "600000", "name": "浦发银行", "market": "SH"}]), provider="fixture")
    provider = LocalDuckDBProvider(store)

    with pytest.raises(NoMarketData, match="本地数据库缺少"):
        provider.history("600000", "2026-08-01", "2026-08-10", interval="1d", adjust="qfq")


def test_local_provider_caches_security_names_across_history_reads(tmp_path, monkeypatch):
    store = DuckDBStore(tmp_path / "local-name-cache.duckdb")
    store.save_stock_list(pd.DataFrame([{"code": "600000", "name": "浦发银行", "market": "SH"}]), provider="fixture")
    store.save_history("600000", "1d", _bars(), adjust="qfq")
    calls = 0
    original = store.load_stock_list

    def counted():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(store, "load_stock_list", counted)
    provider = LocalDuckDBProvider(store)
    first = provider.history("600000", "2026-08-01", "2026-08-10", interval="1d", adjust="qfq")
    second = provider.history("600000", "2026-08-01", "2026-08-10", interval="1d", adjust="qfq")
    assert first.attrs["name"] == second.attrs["name"] == "浦发银行"
    assert calls == 1
