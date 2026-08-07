import pandas as pd
import pytest
from providers.benchmark import BENCHMARKS, BenchmarkProviderChain, resolve_benchmark


def test_benchmark_registry_resolves_ambiguous_index_codes_explicitly():
    assert resolve_benchmark("csi300").eastmoney_secid == "1.000300"
    assert resolve_benchmark("399006").key == "chinext"
    assert BENCHMARKS["sse"].akshare_symbol == "sh000001"


def test_unknown_benchmark_rejected():
    with pytest.raises(ValueError):
        resolve_benchmark("123456")


class Bad:
    name = "bad"
    def history(self, *args, **kwargs):
        raise RuntimeError("boom")


class Good:
    name = "good"
    def history(self, benchmark, start, end, interval="1d"):
        return pd.DataFrame({"datetime":[pd.Timestamp("2026-01-01")],"open":[1],"high":[2],"low":[1],"close":[2],"volume":[1],"amount":[1]})


def test_benchmark_chain_falls_back():
    out = BenchmarkProviderChain([Bad(), Good()]).history("csi300", "20260101", "20260102")
    assert len(out) == 1
