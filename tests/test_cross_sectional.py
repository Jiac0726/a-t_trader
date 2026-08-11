from __future__ import annotations

import numpy as np
import pandas as pd

from calibration.cross_sectional import (
    assess_cross_sectional_promotion_gate,
    combine_labeled_panels,
    cross_sectional_quantile_summary,
    daily_cross_sectional_ic,
    optimize_cross_sectional_weights_on_training,
    summarize_cross_sectional,
    walk_forward_cross_sectional_optimize,
)


def _panel(dates: int = 100, assets: int = 10, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    date_index = pd.bdate_range("2025-01-01", periods=dates)
    for di, day in enumerate(date_index):
        for ai in range(assets):
            amp = 10 + ai * 8 + rng.normal(0, 0.5)
            liq = rng.uniform(10, 90)
            space = rng.uniform(10, 90)
            mr = rng.uniform(10, 90)
            trend = rng.uniform(10, 90)
            risk = rng.uniform(10, 90)
            label = 0.04 * amp + 0.002 * di + rng.normal(0, 0.08)
            score = 0.25 * amp + 0.20 * liq + 0.20 * space + 0.15 * mr + 0.10 * trend + 0.10 * risk
            rows.append({"date": day, "code": f"{ai+1:06d}", "score": score, "amplitude_score": amp, "liquidity_score": liq, "tradable_space_score": space, "mean_reversion_score": mr, "trend_score": trend, "risk_score": risk, "forward_opportunity_pct": label})
    return pd.DataFrame(rows)


def test_daily_cross_sectional_ic_detects_known_ranking_signal():
    panel = _panel(20, 10)
    ic = daily_cross_sectional_ic(panel, score_col="amplitude_score", min_assets=8)
    assert len(ic) == 20
    assert ic["rank_ic"].mean() > 0.90
    summary = summarize_cross_sectional(panel, score_col="amplitude_score", min_assets=8)
    assert summary.mean_rank_ic > 0.90
    assert summary.positive_ic_rate_pct == 100.0


def test_cross_sectional_quantiles_have_higher_top_opportunity():
    summary = cross_sectional_quantile_summary(_panel(30, 15), quantiles=5, min_assets=10)
    assert len(summary) == 5
    assert summary.iloc[-1]["avg_forward_opportunity_pct"] > summary.iloc[0]["avg_forward_opportunity_pct"]


def test_combine_labeled_panels_adds_code_and_deduplicates():
    base = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-01-02"]), "score": [1, 2]})
    out = combine_labeled_panels({"1": base, "2": base})
    assert len(out) == 4
    assert set(out["code"]) == {"000001", "000002"}


def test_cross_sectional_optimizer_learns_amplitude_component():
    panel = _panel(100, 12)
    result = optimize_cross_sectional_weights_on_training(panel.iloc[: 70 * 12], n_candidates=300, seed=10, min_assets=8, gap_dates=5)
    assert max(result["weights"], key=result["weights"].get) == "amplitude_score"
    assert result["weights"]["amplitude_score"] >= 0.30


def test_outer_future_label_changes_do_not_change_first_fold_weights():
    panel = _panel(110, 12)
    metrics_a, weights_a = walk_forward_cross_sectional_optimize(panel, min_train_dates=60, test_dates=20, gap_dates=5, min_assets=8, n_candidates=100, random_trials=20, seed=33)
    assert not metrics_a.empty
    start = pd.Timestamp(metrics_a.iloc[0]["test_start_date"])
    end = pd.Timestamp(metrics_a.iloc[0]["test_end_date"])
    altered = panel.copy()
    altered.loc[altered["date"].between(start, end), "forward_opportunity_pct"] *= -100
    _, weights_b = walk_forward_cross_sectional_optimize(altered, min_train_dates=60, test_dates=20, gap_dates=5, min_assets=8, n_candidates=100, random_trials=20, seed=33)
    assert weights_a.iloc[0].to_dict() == weights_b.iloc[0].to_dict()


def test_min_assets_filters_thin_dates():
    panel = _panel(5, 4)
    assert daily_cross_sectional_ic(panel, min_assets=5).empty


def test_cross_sectional_promotion_gate_rejects_unstable_result():
    metrics = pd.DataFrame({"optimized_mean_daily_ic": [0.25, -0.50], "v01_mean_daily_ic": [0.10, -0.20], "amplitude_mean_daily_ic": [0.12, -0.29], "liquidity_mean_daily_ic": [-0.20, 0.29], "tradable_space_mean_daily_ic": [0.10, -0.31], "optimized_random_pvalue": [0.03, 1.0], "optimized_top_bottom_spread": [0.4, -0.6]})
    assert assess_cross_sectional_promotion_gate(metrics)["promote"] is False


def test_cross_sectional_promotion_gate_can_pass_stable_result():
    metrics = pd.DataFrame({"optimized_mean_daily_ic": [0.30, 0.22, 0.28, 0.25, 0.31], "v01_mean_daily_ic": [0.05, 0.04, 0.06, 0.03, 0.05], "amplitude_mean_daily_ic": [0.12, 0.10, 0.11, 0.09, 0.13], "liquidity_mean_daily_ic": [0.00, 0.01, -0.01, 0.02, 0.00], "tradable_space_mean_daily_ic": [0.08, 0.07, 0.09, 0.06, 0.08], "optimized_random_pvalue": [0.04, 0.08, 0.06, 0.10, 0.05], "optimized_top_bottom_spread": [0.4, 0.3, 0.35, 0.2, 0.45]})
    assert assess_cross_sectional_promotion_gate(metrics)["promote"] is True


def test_cross_sectional_promotion_gate_requires_multiple_oos_folds():
    metrics = pd.DataFrame({"optimized_mean_daily_ic": [0.30], "v01_mean_daily_ic": [0.05], "amplitude_mean_daily_ic": [0.10], "liquidity_mean_daily_ic": [0.00], "tradable_space_mean_daily_ic": [0.08], "optimized_random_pvalue": [0.04], "optimized_top_bottom_spread": [0.4]})
    gate = assess_cross_sectional_promotion_gate(metrics)
    assert gate["promote"] is False
    assert gate["minimum_oos_folds"] is False
