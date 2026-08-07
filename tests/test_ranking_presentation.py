import pandas as pd

from presentation.ranking import enrich_t_ranking


def test_ranking_orders_and_explains_without_changing_score():
    src = pd.DataFrame(
        [
            {
                "code": "000001",
                "score": 72.0,
                "grade": "B",
                "amplitude_score": 70,
                "liquidity_score": 88,
                "tradable_space_score": 75,
                "mean_reversion_score": 60,
                "trend_score": 70,
                "risk_score": 80,
                "avg_amplitude": 3.8,
                "avg_intraday_space": 3.5,
                "median_amount": 2_000_000_000,
                "max_drawdown": -8,
                "error": "",
            },
            {
                "code": "600000",
                "score": 84.0,
                "grade": "B+",
                "amplitude_score": 90,
                "liquidity_score": 82,
                "tradable_space_score": 86,
                "mean_reversion_score": 70,
                "trend_score": 74,
                "risk_score": 76,
                "avg_amplitude": 4.6,
                "avg_intraday_space": 4.2,
                "median_amount": 3_000_000_000,
                "max_drawdown": -10,
                "error": "",
            },
        ]
    )
    out = enrich_t_ranking(src)
    assert out.iloc[0]["code"] == "600000"
    assert out.iloc[0]["rank"] == 1
    assert out.iloc[0]["score"] == 84.0
    assert out.iloc[0]["suitability"] == "优先观察"
    assert "振幅" in out.iloc[0]["reason"]
    assert "成交额" in out.iloc[0]["reason"]


def test_ranking_marks_data_error_and_high_risk():
    src = pd.DataFrame(
        [
            {
                "code": "920001",
                "score": 0,
                "risk_score": 0,
                "avg_amplitude": 0,
                "median_amount": 0,
                "avg_intraday_space": 0,
                "max_drawdown": -100,
                "error": "no market data",
            },
            {
                "code": "300001",
                "score": 68,
                "risk_score": 30,
                "avg_amplitude": 10.0,
                "median_amount": 1_000_000_000,
                "avg_intraday_space": 8.0,
                "max_drawdown": -28,
                "error": "",
            },
        ]
    )
    out = enrich_t_ranking(src)
    bad = out[out["code"] == "920001"].iloc[0]
    risky = out[out["code"] == "300001"].iloc[0]
    assert bad["suitability"] == "数据异常"
    assert bad["risk_label"] == "数据异常"
    assert risky["risk_label"] == "较高"
