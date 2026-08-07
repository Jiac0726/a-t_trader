from __future__ import annotations

import pandas as pd

from dataset.quality import apply_oos_quality_gate


def _panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-01"] * 5),
            "code": ["600519", "000001", "300750", "920002", "601899"],
            "forward_opportunity_pct": [1.0, 1.1, 0.9, 1.2, float("nan")],
            "lineage_adjust": ["qfq"] * 5,
            "lineage_has_unknown": [False, True, False, False, False],
            "lineage_has_estimated_amount": [False, False, True, False, False],
            "lineage_bse_stitched": [False, False, False, True, False],
        }
    )


def _coverage() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["600519", "000001", "300750", "920002", "601899"],
            "adjust": ["none"] * 5,
            "rows_5m": [100] * 5,
            "providers": ["baostock-history"] * 5,
        }
    )


def test_quality_gate_keeps_evidence_and_marks_only_clean_row_eligible():
    out, summary = apply_oos_quality_gate(_panel(), _coverage())
    eligible = out[out["oos_eligible"]]
    assert eligible["code"].tolist() == ["600519"]
    assert len(out) == 5
    assert summary.eligible_rows == 1
    assert summary.excluded_rows == 4
    assert summary.exclusion_reasons["daily_lineage_unknown"] == 1
    assert summary.exclusion_reasons["daily_amount_estimated"] == 1
    assert summary.exclusion_reasons["bse_stitching_unverified"] == 1
    assert summary.exclusion_reasons["label_missing"] == 1


def test_quality_gate_requires_raw_minute_prices_and_known_source():
    coverage = _coverage()
    coverage.loc[0, "adjust"] = "qfq"
    coverage.loc[1, "providers"] = "unknown"
    out, _ = apply_oos_quality_gate(_panel().iloc[:2].copy(), coverage.iloc[:2].copy())
    reasons = dict(zip(out["code"], out["oos_exclusion_reasons"]))
    assert "minute_adjust_not_raw" in reasons["600519"]
    assert "minute_provider_unknown" in reasons["000001"]
