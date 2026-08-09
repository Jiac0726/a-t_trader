from __future__ import annotations

import pandas as pd

from presentation.screening import active_filter_descriptions, apply_active_filters


def sample_ranking() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": "000001",
                "score": 72,
                "avg_amplitude": 4.2,
                "median_amount": 2.0e9,
                "avg_intraday_space": 3.1,
                "max_drawdown": -18,
                "liquidity_score": 78,
                "mean_reversion_score": 66,
                "risk_score": 70,
                "observations": 60,
            },
            {
                "code": "000002",
                "score": 64,
                "avg_amplitude": 9.5,
                "median_amount": 7.0e8,
                "avg_intraday_space": 5.8,
                "max_drawdown": -42,
                "liquidity_score": 55,
                "mean_reversion_score": 48,
                "risk_score": 28,
                "observations": 60,
            },
        ]
    )


def test_only_enabled_non_none_filters_change_results():
    ranking = sample_ranking()
    filters = {
        "min_score": 65,
        "avg_amplitude_range": (2.0, 8.0),
        "min_median_amount": 1.0e9,
        "intraday_space_range": None,
        "max_drawdown_floor": -30,
        "min_liquidity_score": None,
        "min_mean_reversion_score": None,
        "min_risk_score": 40,
        "min_observations": 40,
    }
    out = apply_active_filters(ranking, filters)
    assert out["code"].tolist() == ["000001"]


def test_empty_filter_mapping_preserves_rows():
    ranking = sample_ranking()
    out = apply_active_filters(ranking, {})
    assert out["code"].tolist() == ["000001", "000002"]


def test_filter_description_reports_only_active_filters():
    descriptions = active_filter_descriptions(
        {
            "min_score": 60,
            "avg_amplitude_range": (2.0, 8.0),
            "min_median_amount": None,
            "min_risk_score": 50,
        }
    )
    text = " | ".join(descriptions)
    assert "T Score ≥ 60" in text
    assert "平均振幅 2%–8%" in text
    assert "风险控制得分 ≥ 50" in text
    assert "中位成交额" not in text
