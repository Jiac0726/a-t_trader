from __future__ import annotations

import pandas as pd
import pytest

from providers.benchmark import (
    BENCHMARKS,
    ETFS,
    AkshareBenchmarkProvider,
    BenchmarkProviderChain,
    DemoBenchmarkProvider,
    EastmoneyBenchmarkProvider,
    resolve_benchmark,
    resolve_reference_asset,
)


def test_reference_registry_resolves_index_and_etf_explicitly():
    csi = resolve_benchmark("csi300")
    assert csi.eastmoney_secids == ("2.000300", "1.000300")
    assert resolve_benchmark("399006").key == "chinext"
    assert BENCHMARKS["sse"].akshare_symbol == "sh000001"
    assert resolve_reference_asset("510300").kind == "etf"
    assert ETFS["csi300_etf_sh"].eastmoney_secid == "1.510300"


def test_unknown_benchmark_rejected():
    with pytest.raises(ValueError):
        resolve_benchmark("123456")
    with pytest.raises(ValueError):
        resolve_benchmark("csi300_etf_sh")


class Bad:
    name = "bad"

    def history(self, *args, **kwargs):
        raise RuntimeError("boom")


class Good:
    name = "good"

    def history(self, benchmark, start, end, interval="1d", adjust="qfq"):
        df = pd.DataFrame(
            {
                "datetime": [pd.Timestamp("2026-01-01")],
                "open": [1],
                "high": [2],
                "low": [1],
                "close": [2],
                "volume": [1],
                "amount": [1],
            }
        )
        df.attrs["kind"] = "index"
        return df


def test_reference_chain_falls_back():
    out = BenchmarkProviderChain([Bad(), Good()]).history("csi300", "20260101", "20260102")
    assert len(out) == 1


def test_eastmoney_csi_index_tries_declared_secid_candidates():
    provider = EastmoneyBenchmarkProvider(min_interval=0)
    seen = []

    def fake_get(url, params):
        seen.append(params["secid"])
        if params["secid"] == "2.000300":
            return {"data": None}
        return {"data": {"klines": ["2026-01-02,10,11,12,9,100,1000,30,10,1,0"]}}

    provider.client._get_json = fake_get
    out = provider.history("csi300", "20260101", "20260103")
    assert seen == ["2.000300", "1.000300"]
    assert out.attrs["eastmoney_secid"] == "1.000300"
    assert out.attrs["kind"] == "index"


def test_demo_reference_supports_index_and_etf_offline():
    demo = DemoBenchmarkProvider()
    index = demo.history("csi300", "2026-01-01", "2026-02-01")
    etf = demo.history("csi300_etf_sh", "2026-01-01", "2026-02-01")
    assert not index.empty and index.attrs["kind"] == "index"
    assert not etf.empty and etf.attrs["kind"] == "etf"


def test_akshare_reference_dispatches_etf_without_real_dependency():
    class FakeAk:
        @staticmethod
        def fund_etf_hist_em(**kwargs):
            assert kwargs["symbol"] == "510300"
            return pd.DataFrame(
                {
                    "日期": ["2026-01-02"],
                    "开盘": [1.0],
                    "收盘": [1.1],
                    "最高": [1.2],
                    "最低": [0.9],
                    "成交量": [100],
                    "成交额": [1000],
                }
            )

    provider = AkshareBenchmarkProvider()
    provider._ak = lambda: FakeAk()
    out = provider.history("csi300_etf_sh", "20260101", "20260103")
    assert out.attrs["kind"] == "etf"
    assert len(out) == 1
