import pandas as pd
import pytest

from scanner.market_scanner import select_rankable_universe


class UniverseProvider:
    name = "universe"

    def __init__(self, frame):
        self.frame = frame

    def stock_list(self):
        return self.frame.copy()


class ScoreStore:
    def __init__(self, frame=None):
        self.frame = pd.DataFrame() if frame is None else frame

    def load_scores(self):
        return self.frame.copy()


def test_rankable_universe_prefers_current_amount():
    provider = UniverseProvider(
        pd.DataFrame(
            [
                {"code": "000001", "name": "A", "amount": 1_000_000_000},
                {"code": "000002", "name": "B", "amount": 3_000_000_000},
                {"code": "000003", "name": "C", "amount": 2_000_000_000},
            ]
        )
    )
    out = select_rankable_universe(provider, limit=2)
    assert out["code"].tolist() == ["000002", "000003"]
    assert out.attrs["selection_basis"] == "current_amount"


def test_rankable_universe_falls_back_to_cached_scores():
    provider = UniverseProvider(
        pd.DataFrame(
            [
                {"code": "000001", "name": "A"},
                {"code": "000002", "name": "B"},
                {"code": "000003", "name": "C"},
            ]
        )
    )
    store = ScoreStore(
        pd.DataFrame(
            [
                {"code": "000001", "score": 66, "median_amount": 1},
                {"code": "000002", "score": 81, "median_amount": 1},
                {"code": "000003", "score": 74, "median_amount": 1},
            ]
        )
    )
    out = select_rankable_universe(provider, store=store, limit=2)
    assert out["code"].tolist() == ["000002", "000003"]
    assert out.attrs["selection_basis"] == "cached_t_score"


def test_rankable_universe_refuses_arbitrary_first_n_without_prefilter():
    provider = UniverseProvider(pd.DataFrame([{"code": f"{i:06d}", "name": f"S{i}"} for i in range(200)]))
    with pytest.raises(ValueError, match="不能按股票代码顺序"):
        select_rankable_universe(provider, store=ScoreStore(), limit=100)
