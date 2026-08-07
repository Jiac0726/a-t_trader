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
