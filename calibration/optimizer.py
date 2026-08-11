from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from calibration.walkforward import expanding_walk_forward, rank_ic

COMPONENT_COLUMNS = (
    "amplitude_score",
    "liquidity_score",
    "tradable_space_score",
    "mean_reversion_score",
    "trend_score",
    "risk_score",
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "amplitude_score": 0.25,
    "liquidity_score": 0.20,
    "tradable_space_score": 0.20,
    "mean_reversion_score": 0.15,
    "trend_score": 0.10,
    "risk_score": 0.10,
}


@dataclass(slots=True)
class WeightOptimizationResult:
    weights: dict[str, float]
    objective: float
    mean_inner_ic: float
    std_inner_ic: float
    valid_inner_folds: int
    candidates_evaluated: int
    used_inner_cv: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _validate_component_columns(frame: pd.DataFrame) -> None:
    missing = [c for c in COMPONENT_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"missing component score columns: {', '.join(missing)}")


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    values = np.array([float(weights.get(c, 0.0)) for c in COMPONENT_COLUMNS], dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("weights must be finite and non-negative")
    total = float(values.sum())
    if total <= 0:
        raise ValueError("at least one weight must be positive")
    values /= total
    return {c: float(v) for c, v in zip(COMPONENT_COLUMNS, values, strict=True)}


def apply_weights(frame: pd.DataFrame, weights: Mapping[str, float], output_col: str = "optimized_score") -> pd.DataFrame:
    _validate_component_columns(frame)
    normalized = normalize_weights(weights)
    out = frame.copy()
    score = np.zeros(len(out), dtype=float)
    for col, weight in normalized.items():
        score += pd.to_numeric(out[col], errors="coerce").to_numpy(dtype=float) * weight
    out[output_col] = score
    return out


def generate_weight_candidates(n_candidates: int = 256, seed: int = 42, max_weight: float = 0.55) -> list[dict[str, float]]:
    """Generate deterministic non-negative simplex candidates."""
    n_candidates = max(2, int(n_candidates))
    max_weight = float(max_weight)
    if not (1.0 / len(COMPONENT_COLUMNS) <= max_weight <= 1.0):
        raise ValueError("max_weight must be between equal-weight share and 1")
    base = normalize_weights(DEFAULT_WEIGHTS)
    equal = {c: 1.0 / len(COMPONENT_COLUMNS) for c in COMPONENT_COLUMNS}
    candidates: list[dict[str, float]] = [base, equal]
    if n_candidates <= 2:
        return candidates[:n_candidates]
    rng = np.random.default_rng(int(seed))
    attempts = 0
    max_attempts = max(1000, n_candidates * 100)
    seen = {tuple(round(x[c], 8) for c in COMPONENT_COLUMNS) for x in candidates}
    alpha = np.full(len(COMPONENT_COLUMNS), 1.5, dtype=float)
    while len(candidates) < n_candidates and attempts < max_attempts:
        attempts += 1
        values = rng.dirichlet(alpha)
        if float(values.max()) > max_weight:
            continue
        key = tuple(round(float(v), 8) for v in values)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({c: float(v) for c, v in zip(COMPONENT_COLUMNS, values, strict=True)})
    if len(candidates) < n_candidates:
        for step in np.linspace(0.05, 0.95, n_candidates * 2):
            if len(candidates) >= n_candidates:
                break
            values = np.array([base[c] for c in COMPONENT_COLUMNS]) * step + (1.0 - step) / len(COMPONENT_COLUMNS)
            if float(values.max()) > max_weight:
                continue
            key = tuple(round(float(v), 8) for v in values)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({c: float(v) for c, v in zip(COMPONENT_COLUMNS, values, strict=True)})
    return candidates[:n_candidates]


def _candidate_inner_ics(train: pd.DataFrame, weights: Mapping[str, float], label_col: str, gap: int, inner_min_train: int, inner_test_size: int) -> list[float]:
    weighted = apply_weights(train, weights, output_col="candidate_score")
    folds = expanding_walk_forward(len(weighted), min_train_size=inner_min_train, test_size=inner_test_size, gap=gap)
    ics: list[float] = []
    for fold in folds:
        test = weighted.iloc[fold.test_start : fold.test_end + 1]
        ic = rank_ic(test, score_col="candidate_score", label_col=label_col)
        if np.isfinite(ic):
            ics.append(float(ic))
    return ics


def optimize_weights_on_training(train: pd.DataFrame, label_col: str = "forward_opportunity_pct", n_candidates: int = 256, seed: int = 42, max_weight: float = 0.55, gap: int = 5, inner_min_train: int | None = None, inner_test_size: int | None = None, stability_penalty: float = 0.20) -> WeightOptimizationResult:
    """Optimize only on a supplied training window; outer test data is never used."""
    _validate_component_columns(train)
    if label_col not in train.columns:
        raise ValueError(f"missing label column: {label_col}")
    x = train.copy().dropna(subset=[*COMPONENT_COLUMNS, label_col]).reset_index(drop=True)
    if len(x) < 8:
        raise ValueError("not enough training rows to optimize weights")
    if inner_min_train is None:
        inner_min_train = max(20, min(60, len(x) // 2))
    if inner_test_size is None:
        remaining = max(1, len(x) - inner_min_train - gap)
        inner_test_size = max(5, min(20, remaining // 2 if remaining >= 2 else 1))
    candidates = generate_weight_candidates(n_candidates=n_candidates, seed=seed, max_weight=max_weight)
    best: WeightOptimizationResult | None = None
    for weights in candidates:
        ics: list[float] = []
        can_inner_cv = len(x) >= inner_min_train + gap + max(1, inner_test_size)
        if can_inner_cv:
            ics = _candidate_inner_ics(x, weights, label_col, gap, inner_min_train, inner_test_size)
        used_inner_cv = len(ics) > 0
        if used_inner_cv:
            mean_ic = float(np.mean(ics))
            std_ic = float(np.std(ics, ddof=0))
            objective = mean_ic - float(stability_penalty) * std_ic
        else:
            weighted = apply_weights(x, weights, output_col="candidate_score")
            train_ic = rank_ic(weighted, score_col="candidate_score", label_col=label_col)
            mean_ic = float(train_ic) if np.isfinite(train_ic) else -1.0
            std_ic = 0.0
            objective = mean_ic
        current = WeightOptimizationResult(normalize_weights(weights), float(objective), float(mean_ic), float(std_ic), len(ics), len(candidates), used_inner_cv)
        if best is None or current.objective > best.objective + 1e-12:
            best = current
    assert best is not None
    return best


def summarize_model_comparison(fold_metrics: pd.DataFrame) -> pd.DataFrame:
    models = {"optimized": "optimized_rank_ic", "v0.1_fixed": "v01_rank_ic", "amplitude": "amplitude_rank_ic", "liquidity": "liquidity_rank_ic", "tradable_space": "tradable_space_rank_ic", "random_null_mean": "random_rank_ic"}
    rows: list[dict] = []
    for model, col in models.items():
        if col not in fold_metrics.columns:
            continue
        values = pd.to_numeric(fold_metrics[col], errors="coerce").dropna()
        if values.empty:
            continue
        rows.append({"model": model, "folds": int(len(values)), "mean_rank_ic": round(float(values.mean()), 4), "median_rank_ic": round(float(values.median()), 4), "positive_fold_rate_pct": round(float((values > 0).mean() * 100), 2), "worst_fold_ic": round(float(values.min()), 4)})
    return pd.DataFrame(rows).sort_values("mean_rank_ic", ascending=False).reset_index(drop=True)


def summarize_weight_stability(weight_history: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for component in COMPONENT_COLUMNS:
        col = f"w_{component}"
        if col not in weight_history.columns:
            continue
        values = pd.to_numeric(weight_history[col], errors="coerce").dropna()
        if values.empty:
            continue
        rows.append({"component": component, "mean_weight": round(float(values.mean()), 4), "std_weight": round(float(values.std(ddof=0)), 4), "min_weight": round(float(values.min()), 4), "max_weight": round(float(values.max()), 4)})
    return pd.DataFrame(rows).sort_values("mean_weight", ascending=False).reset_index(drop=True)


def assess_promotion_gate(fold_metrics: pd.DataFrame, min_positive_fold_rate: float = 60.0, min_ic_improvement: float = 0.03, max_median_random_pvalue: float = 0.20, min_oos_folds: int = 3) -> dict[str, object]:
    if fold_metrics.empty:
        return {"promote": False, "reason": "no outer OOS folds"}
    opt = pd.to_numeric(fold_metrics.get("optimized_rank_ic"), errors="coerce").dropna()
    if opt.empty:
        return {"promote": False, "reason": "no valid optimized OOS IC"}
    baseline_means: dict[str, float] = {}
    for col in ["v01_rank_ic", "amplitude_rank_ic", "liquidity_rank_ic", "tradable_space_rank_ic"]:
        values = pd.to_numeric(fold_metrics.get(col), errors="coerce").dropna()
        if not values.empty:
            baseline_means[col] = float(values.mean())
    strongest_baseline = max(baseline_means.values()) if baseline_means else float("-inf")
    opt_mean = float(opt.mean())
    positive_rate = float((opt > 0).mean() * 100)
    pvals = pd.to_numeric(fold_metrics.get("optimized_random_pvalue"), errors="coerce").dropna()
    median_p = float(pvals.median()) if not pvals.empty else 1.0
    checks = {"minimum_oos_folds": len(opt) >= int(min_oos_folds), "beats_strongest_baseline": opt_mean >= strongest_baseline + float(min_ic_improvement), "positive_fold_rate": positive_rate >= float(min_positive_fold_rate), "random_null_significance": median_p <= float(max_median_random_pvalue)}
    promote = all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    return {"promote": promote, "reason": "all OOS gates passed" if promote else "failed: " + ", ".join(failed), "oos_folds": int(len(opt)), "required_oos_folds": int(min_oos_folds), "optimized_mean_rank_ic": round(opt_mean, 4), "strongest_baseline_mean_rank_ic": round(strongest_baseline, 4) if np.isfinite(strongest_baseline) else np.nan, "positive_fold_rate_pct": round(positive_rate, 2), "median_random_pvalue": round(median_p, 4), **checks}


def _random_null_distribution(test: pd.DataFrame, label_col: str, rng: np.random.Generator, trials: int) -> np.ndarray:
    labels = pd.to_numeric(test[label_col], errors="coerce")
    valid = labels.notna().to_numpy()
    n = int(valid.sum())
    if n < 3 or labels[valid].nunique() < 2:
        return np.array([], dtype=float)
    frame = pd.DataFrame({label_col: labels[valid].to_numpy(dtype=float)})
    values: list[float] = []
    for _ in range(max(1, int(trials))):
        frame["random_score"] = rng.normal(size=n)
        ic = rank_ic(frame, score_col="random_score", label_col=label_col)
        if np.isfinite(ic):
            values.append(float(ic))
    return np.asarray(values, dtype=float)


def walk_forward_optimize_weights(labeled: pd.DataFrame, min_train_size: int = 60, test_size: int = 20, gap: int = 5, label_col: str = "forward_opportunity_pct", n_candidates: int = 256, seed: int = 42, max_weight: float = 0.55, random_trials: int = 200) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Outer walk-forward optimization with simple and random-null benchmarks."""
    _validate_component_columns(labeled)
    required = ["date", label_col, *COMPONENT_COLUMNS]
    missing = [c for c in required if c not in labeled.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    x = labeled.copy().sort_values("date").dropna(subset=[label_col, *COMPONENT_COLUMNS]).reset_index(drop=True)
    folds = expanding_walk_forward(len(x), min_train_size=min_train_size, test_size=test_size, gap=gap)
    metric_rows: list[dict] = []
    weight_rows: list[dict] = []
    oos_parts: list[pd.DataFrame] = []
    for fold in folds:
        train = x.iloc[fold.train_start : fold.train_end + 1].copy()
        test = x.iloc[fold.test_start : fold.test_end + 1].copy()
        if train.empty or test.empty:
            continue
        opt = optimize_weights_on_training(train, label_col=label_col, n_candidates=n_candidates, seed=seed + fold.fold, max_weight=max_weight, gap=gap)
        test = apply_weights(test, opt.weights, output_col="optimized_score")
        rng = np.random.default_rng(seed + 10000 + fold.fold)
        v01_col = "score" if "score" in test.columns else None
        optimized_ic = rank_ic(test, score_col="optimized_score", label_col=label_col)
        v01_ic = rank_ic(test, score_col=v01_col, label_col=label_col) if v01_col else float("nan")
        amplitude_ic = rank_ic(test, score_col="amplitude_score", label_col=label_col)
        liquidity_ic = rank_ic(test, score_col="liquidity_score", label_col=label_col)
        space_ic = rank_ic(test, score_col="tradable_space_score", label_col=label_col)
        random_null = _random_null_distribution(test, label_col, rng, random_trials)
        random_ic = float(random_null.mean()) if len(random_null) else float("nan")
        random_std = float(random_null.std(ddof=0)) if len(random_null) else float("nan")
        if len(random_null) and np.isfinite(optimized_ic):
            random_percentile = float((random_null <= optimized_ic).mean() * 100)
            random_pvalue = float((1 + np.sum(random_null >= optimized_ic)) / (len(random_null) + 1))
        else:
            random_percentile = float("nan")
            random_pvalue = float("nan")
        metric_rows.append({"fold": fold.fold, "train_start_date": str(pd.Timestamp(train.iloc[0]["date"]).date()), "train_end_date": str(pd.Timestamp(train.iloc[-1]["date"]).date()), "test_start_date": str(pd.Timestamp(test.iloc[0]["date"]).date()), "test_end_date": str(pd.Timestamp(test.iloc[-1]["date"]).date()), "train_rows": len(train), "test_rows": len(test), "train_objective": round(opt.objective, 4), "train_mean_inner_ic": round(opt.mean_inner_ic, 4), "train_ic_std": round(opt.std_inner_ic, 4), "inner_valid_folds": opt.valid_inner_folds, "optimized_rank_ic": round(optimized_ic, 4) if np.isfinite(optimized_ic) else np.nan, "v01_rank_ic": round(v01_ic, 4) if np.isfinite(v01_ic) else np.nan, "amplitude_rank_ic": round(amplitude_ic, 4) if np.isfinite(amplitude_ic) else np.nan, "liquidity_rank_ic": round(liquidity_ic, 4) if np.isfinite(liquidity_ic) else np.nan, "tradable_space_rank_ic": round(space_ic, 4) if np.isfinite(space_ic) else np.nan, "random_rank_ic": round(random_ic, 4) if np.isfinite(random_ic) else np.nan, "random_rank_ic_std": round(random_std, 4) if np.isfinite(random_std) else np.nan, "optimized_random_percentile": round(random_percentile, 2) if np.isfinite(random_percentile) else np.nan, "optimized_random_pvalue": round(random_pvalue, 4) if np.isfinite(random_pvalue) else np.nan})
        weight_rows.append({"fold": fold.fold, **{f"w_{c}": round(v, 6) for c, v in opt.weights.items()}})
        test["fold"] = fold.fold
        oos_parts.append(test)
    metrics = pd.DataFrame(metric_rows)
    weights = pd.DataFrame(weight_rows)
    oos = pd.concat(oos_parts, ignore_index=True) if oos_parts else pd.DataFrame()
    return metrics, weights, oos
