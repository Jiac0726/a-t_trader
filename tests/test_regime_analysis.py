from __future__ import annotations

import pandas as pd

from calibration.regime_analysis import regime_coverage, summarize_cross_sectional_by_regime


def test_regime_cross_sectional_summary_separates_states():
    rows = []
    for di, day in enumerate(pd.bdate_range("2026-01-01", periods=12)):
        regime = "range" if di < 6 else "trend_up"
        for ai in range(8):
            score = float(ai)
            label = score if regime == "range" else -score
            rows.append({"date": day, "code": f"{ai:06d}", "market_regime": regime, "score": score, "forward_opportunity_pct": label})
    panel = pd.DataFrame(rows)
    out = summarize_cross_sectional_by_regime(panel, min_assets=5)
    values = out.set_index("regime")["mean_rank_ic"].to_dict()
    assert values["range"] > 0.9
    assert values["trend_up"] < -0.9
    coverage = regime_coverage(panel)
    assert set(coverage["regime"]) == {"range", "trend_up"}
