from providers.chain import ProviderChain
from providers.demo import DemoProvider


def test_demo_universe_and_chain():
    chain = ProviderChain([DemoProvider()])
    stocks = chain.stock_list()
    assert {"code", "name", "market"}.issubset(stocks.columns)
    assert "300059" in set(stocks["code"])
