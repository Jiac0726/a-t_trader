from datetime import date, timedelta

from data.validator import validate_ohlcv
from features.daily import daily_features
from providers.demo import DemoProvider
from scoring.t_score import amplitude_score, build_t_score


def test_amplitude_has_sweet_spot():
    assert amplitude_score(4.5) > amplitude_score(1.0)
    assert amplitude_score(4.5) > amplitude_score(12.0)


def test_demo_pipeline():
    p = DemoProvider()
    end = date(2026, 8, 7)
    df = validate_ohlcv(p.history("300059", end - timedelta(days=140), end))
    features = daily_features(df, 60)
    result = build_t_score("300059", "demo", features, provider=p.name)
    assert 0 <= result.score <= 100
    assert result.observations == 60
    assert result.latest_close > 0
