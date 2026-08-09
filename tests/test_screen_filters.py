import pandas as pd

from presentation.filters import ScoreFilterConfig, apply_score_filters


def sample_scores():
    return pd.DataFrame(
        [
            {
                "code": "000001", "score": 82, "grade": "B+", "avg_amplitude": 4.2,
                "avg_intraday_space": 3.4, "median_amount": 4_000_000_000, "max_drawdown": -12,
                "latest_close": 12.5, "observations": 60, "amplitude_score": 90,
                "liquidity_score": 80, "tradable_space_score": 68, "mean_reversion_score": 72,
                "trend_score": 75, "risk_score": 78, "error": "",
            },
            {
                "code": "000002", "score": 70, "grade": "B", "avg_amplitude": 7.8,
                "avg_intraday_space": 5.0, "median_amount": 900_000_000, "max_drawdown": -31,
                "latest_close": 32.0, "observations": 60, "amplitude_score": 78,
                "liquidity_score": 55, "tradable_space_score": 100, "mean_reversion_score": 65,
                "trend_score": 48, "risk_score": 38, "error": "",
            },
            {
                "code": "000003", "score": 90, "grade": "A", "avg_amplitude": 3.0,
                "avg_intraday_space": 2.0, "median_amount": 6_000_000_000, "max_drawdown": -8,
                "latest_close": 8.0, "observations": 10, "amplitude_score": 75,
                "liquidity_score": 90, "tradable_space_score": 40, "mean_reversion_score": 80,
                "trend_score": 88, "risk_score": 91, "error": "upstream failed",
            },
        ]
    )


def test_filters_combine_enabled_rules_with_and():
    config = ScoreFilterConfig(
        min_score=75,
        min_avg_amplitude=3.5,
        max_avg_amplitude=6.0,
        min_median_amount=2_000_000_000,
        max_abs_drawdown=20,
        min_risk_score=60,
    )
    out, audit = apply_score_filters(sample_scores(), config)
    assert out["code"].tolist() == ["000001"]
    assert not audit.empty
    assert audit["removed"].sum() >= 2


def test_filter_can_use_subscores_and_grade_without_recomputing_t_score():
    config = ScoreFilterConfig(
        min_liquidity_score=70,
        min_tradable_space_score=60,
        grades=("A", "B+"),
        exclude_errors=False,
    )
    out, _ = apply_score_filters(sample_scores(), config)
    assert out["code"].tolist() == ["000001"]


def test_error_rows_are_excluded_by_default():
    out, audit = apply_score_filters(sample_scores(), ScoreFilterConfig())
    assert out["code"].tolist() == ["000001", "000002"]
    assert audit.iloc[0]["rule"] == "排除数据错误"
