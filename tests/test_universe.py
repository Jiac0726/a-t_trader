from providers.chain import ProviderChain
from providers.demo import DemoProvider


def test_demo_universe_and_chain():
    chain = ProviderChain([DemoProvider()])
    stocks = chain.stock_list()
    assert {"code", "name", "market"}.issubset(stocks.columns)
    assert "300059" in set(stocks["code"])


def test_nested_provider_chain_preserves_leaf_provider_attr():
    import pandas as pd
    from datetime import date
    from providers.base import MarketDataProvider

    class Leaf(MarketDataProvider):
        name = "leaf"
        def history(self, code, start, end, interval="1d", adjust="qfq"):
            df = pd.DataFrame({
                "datetime": [pd.Timestamp("2026-01-01")],
                "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                "volume": [1.0], "amount": [1.0],
            })
            df.attrs["provider"] = self.name
            return df

    inner = ProviderChain([Leaf()])
    outer = ProviderChain([inner])
    df = outer.history("600000", date(2026, 1, 1), date(2026, 1, 2))
    assert df.attrs["provider"] == "leaf"


def test_auto_price_provider_keeps_tencent_without_identity_provider(monkeypatch):
    from app.cli import make_provider
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = make_provider("auto", retries=0)
    names = [getattr(x, "name", type(x).__name__) for x in provider.providers]
    assert "baostock-history" in names
    assert "tencent-history" in names
    assert "tushare-history" not in names
    assert "official-exchange-universe" not in names
    assert "baostock-snapshot-universe" not in names


def test_auto_price_provider_adds_tushare_only_when_token_configured(monkeypatch):
    from app.cli import make_provider
    monkeypatch.setenv("TUSHARE_TOKEN", "configured-for-test")
    provider = make_provider("auto", retries=0)
    names = [getattr(x, "name", type(x).__name__) for x in provider.providers]
    assert "tushare-history" in names
    assert "official-exchange-universe" not in names


def test_auto_universe_provider_prefers_baostock_identity_chain(monkeypatch):
    from app.cli import make_universe_provider
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = make_universe_provider("auto", retries=0)
    names = [getattr(x, "name", type(x).__name__) for x in provider.providers]
    assert names[0] == "baostock-snapshot-universe"
    assert "eastmoney-direct" in names
    assert "akshare" in names
    assert names[-1] == "official-exchange-universe"


def test_auto_universe_provider_includes_optional_tushare(monkeypatch):
    from app.cli import make_universe_provider
    monkeypatch.setenv("TUSHARE_TOKEN", "configured-for-test")
    provider = make_universe_provider("auto", retries=0)
    names = [getattr(x, "name", type(x).__name__) for x in provider.providers]
    assert "tushare-history" in names
    assert names[0] == "baostock-snapshot-universe"
