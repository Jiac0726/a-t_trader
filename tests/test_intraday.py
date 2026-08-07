from datetime import date, timedelta

from data.validator import validate_ohlcv
from features.intraday import intraday_opportunity_features
from providers.demo import DemoProvider


def test_intraday_features():
    p = DemoProvider()
    end = date(2026, 8, 7)
    df = validate_ohlcv(p.history("300059", end - timedelta(days=10), end, interval="5m"))
    out = intraday_opportunity_features(df, threshold_pct=0.5)
    assert out["days"] > 0
    assert out["avg_opportunities"] >= 0
