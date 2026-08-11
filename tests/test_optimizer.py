from __future__ import annotations

import numpy as np
import pandas as pd

from calibration.optimizer import (
    COMPONENT_COLUMNS,
    assess_promotion_gate,
    generate_weight_candidates,
    optimize_weights_on_training,
    summarize_model_comparison,
    summarize_weight_stability,
    walk_forward_optimize_weights,
)


def _synthetic_frame(n: int = 180, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    amp = np.linspace(5, 95, n) + rng.normal(0, 2, n)
    frame = pd.DataFrame({"date": pd.bdate_range("2025-01-01", periods=n), "amplitude_score": amp, "liquidity_score": rng.uniform(10, 90, n), "tradable_space_score": rng.uniform(10, 90, n), "mean_reversion_score": rng.uniform(10, 90, n), "trend_score": rng.uniform(10, 90, n), "risk_score": rng.uniform(10, 90, n)})
    frame["forward_opportunity_pct"] = 0.03 * amp + rng.normal(0, 0.15, n)
    frame["score"] = 0.25 * frame["amplitude_score"] + 0.20 * frame["liquidity_score"] + 0.20 * frame["tradable_space_score"] + 0.15 * frame["mean_reversion_score"] + 0.10 * frame["trend_score"] + 0.10 * frame["risk_score"]
    return frame


def test_generated_weights_are_valid_and_deterministic():
    first = generate_weight_candidates(n_candidates=40, seed=123, max_weight=0.55)
    second = generate_weight_candidates(n_candidates=40, seed=123, max_weight=0.55)
    assert first == second
    assert len(first) == 40
    for weights in first:
        assert set(weights) == set(COMPONENT_COLUMNS)
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        assert min(weights.values()) >= 0
        assert max(weights.values()) <= 0.55 + 1e-12


def test_optimizer_learns_dominant_synthetic_component():
    frame = _synthetic_frame(180)
    result = optimize_weights_on_training(frame.iloc[:130], n_candidates=500, seed=11, max_weight=0.55, gap=5)
    assert max(result.weights, key=result.weights.get) == "amplitude_score"
    assert result.weights["amplitude_score"] >= 0.30
    assert result.candidates_evaluated == 500


def test_outer_test_labels_do_not_change_first_fold_weights():
    frame = _synthetic_frame(180)
    metrics_a, weights_a, _ = walk_forward_optimize_weights(frame, min_train_size=80, test_size=25, gap=5, n_candidates=180, seed=21)
    first_test_start = pd.Timestamp(metrics_a.iloc[0]["test_start_date"])
    first_test_end = pd.Timestamp(metrics_a.iloc[0]["test_end_date"])
    altered = frame.copy()
    mask = altered["date"].between(first_test_start, first_test_end)
    altered.loc[mask, "forward_opportunity_pct"] *= -100.0
    _, weights_b, _ = walk_forward_optimize_weights(altered, min_train_size=80, test_size=25, gap=5, n_candidates=180, seed=21)
    assert weights_a.iloc[0].to_dict() == weights_b.iloc[0].to_dict()


def test_walk_forward_optimizer_outputs_benchmarks_and_oos_rows():
    frame = _synthetic_frame(180)
    metrics, weights, oos = walk_forward_optimize_weights(frame, min_train_size=80, test_size=25, gap=5, n_candidates=120, seed=8)
    assert not metrics.empty and not oos.empty and len(metrics) == len(weights)
    for col in ["optimized_rank_ic", "v01_rank_ic", "amplitude_rank_ic", "liquidity_rank_ic", "tradable_space_rank_ic", "random_rank_ic"]:
        assert col in metrics.columns
    summary = summarize_model_comparison(metrics)
    assert {"optimized", "v0.1_fixed", "amplitude", "liquidity", "tradable_space", "random_null_mean"}.issubset(set(summary["model"]))
    assert len(summarize_weight_stability(weights)) == len(COMPONENT_COLUMNS)
    assert "optimized_random_pvalue" in metrics.columns


def test_optimizer_is_reproducible_for_same_seed():
    frame = _synthetic_frame(160)
    args = dict(min_train_size=70, test_size=20, gap=5, n_candidates=100, seed=77)
    metrics_a, weights_a, _ = walk_forward_optimize_weights(frame, **args)
    metrics_b, weights_b, _ = walk_forward_optimize_weights(frame, **args)
    pd.testing.assert_frame_equal(metrics_a, metrics_b)
    pd.testing.assert_frame_equal(weights_a, weights_b)


def test_promotion_gate_rejects_unconvincing_oos_result():
    metrics = pd.DataFrame({"optimized_rank_ic": [0.02, -0.01, 0.03], "v01_rank_ic": [0.01, 0.02, 0.01], "amplitude_rank_ic": [0.10, 0.05, 0.08], "liquidity_rank_ic": [0.00, -0.03, 0.01], "tradable_space_rank_ic": [0.04, 0.02, 0.03], "optimized_random_pvalue": [0.4, 0.5, 0.35]})
    gate = assess_promotion_gate(metrics)
    assert gate["promote"] is False
    assert "failed:" in gate["reason"]


def test_promotion_gate_can_pass_strong_stable_oos_result():
    metrics = pd.DataFrame({"optimized_rank_ic": [0.30, 0.25, 0.28, 0.22, 0.35], "v01_rank_ic": [0.05, 0.06, 0.03, 0.08, 0.02], "amplitude_rank_ic": [0.10, 0.11, 0.08, 0.12, 0.09], "liquidity_rank_ic": [0.01, 0.00, 0.02, -0.01, 0.03], "tradable_space_rank_ic": [0.07, 0.06, 0.09, 0.05, 0.08], "optimized_random_pvalue": [0.04, 0.08, 0.06, 0.10, 0.05]})
    assert assess_promotion_gate(metrics)["promote"] is True


def test_promotion_gate_requires_multiple_oos_folds():
    metrics = pd.DataFrame({"optimized_rank_ic": [0.30], "v01_rank_ic": [0.05], "amplitude_rank_ic": [0.10], "liquidity_rank_ic": [0.01], "tradable_space_rank_ic": [0.07], "optimized_random_pvalue": [0.04]})
    gate = assess_promotion_gate(metrics)
    assert gate["promote"] is False
    assert gate["minimum_oos_folds"] is False
