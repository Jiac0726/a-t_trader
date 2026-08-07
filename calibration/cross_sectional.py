from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from calibration.optimizer import COMPONENT_COLUMNS, apply_weights, generate_weight_candidates, normalize_weights


@dataclass(slots=True)
class CrossSectionalSummary:
    dates: int
    observations: int
    mean_rank_ic: float
    median_rank_ic: float
    positive_ic_rate_pct: float
    rank_ic_std: float
    rank_ic_ir: float
    mean_top_bottom_spread: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class CrossSectionalFold:
    fold: int
    train_start_date: str
    train_end_date: str
    test_start_date: str
    test_end_date: str
    train_dates: int
    test_dates: int

    def to_dict(self) -> dict:
        return asdict(self)


def combine_labeled_panels(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Combine per-stock labeled histories into a date × stock research panel."""
    parts: list[pd.DataFrame] = []
    for code, frame in frames.items():
        if frame is None or frame.empty:
            continue
        part = frame.copy()
        part["code"] = str(code).zfill(6)
        part["date"] = pd.to_datetime(part["date"], errors="coerce").dt.normalize()
        parts.append(part)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    out = out.dropna(subset=["date", "code"]).drop_duplicates(["date", "code"], keep="last")
    return out.sort_values(["date", "code"]).reset_index(drop=True)


def _average_ranks(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    n = len(values)
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    ranks = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i + 1
        while j < n and sorted_values[j] == sorted_values[i]:
            j += 1
        ranks[order[i:j]] = (i + 1 + j) / 2.0
        i = j
    return ranks


def _rank_corr_from_ranks(x_ranks: np.ndarray, y_ranks: np.ndarray) -> float:
    x = np.asarray(x_ranks, dtype=float)
    y = np.asarray(y_ranks, dtype=float)
    if len(x) < 3 or len(x) != len(y):
        return float("nan")
    dx = x - x.mean()
    dy = y - y.mean()
    denom = float(np.sqrt(np.dot(dx, dx) * np.dot(dy, dy)))
    if denom <= 0:
        return float("nan")
    return float(np.dot(dx, dy) / denom)


def _spearman(x: pd.Series, y: pd.Series) -> float:
    frame = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce"), "y": pd.to_numeric(y, errors="coerce")}).dropna()
    if len(frame) < 3 or frame["x"].nunique() < 2 or frame["y"].nunique() < 2:
        return float("nan")
    return _rank_corr_from_ranks(_average_ranks(frame["x"].to_numpy(dtype=float)), _average_ranks(frame["y"].to_numpy(dtype=float)))


def daily_cross_sectional_ic(panel: pd.DataFrame, score_col: str = "score", label_col: str = "forward_opportunity_pct", min_assets: int = 5) -> pd.DataFrame:
    if panel is None or panel.empty:
        return pd.DataFrame(columns=["date", "assets", "rank_ic"])
    required = {"date", "code", score_col, label_col}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    rows: list[dict] = []
    x = panel.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    for day, group in x.groupby("date", sort=True):
        clean = group[["code", score_col, label_col]].copy()
        clean[score_col] = pd.to_numeric(clean[score_col], errors="coerce")
        clean[label_col] = pd.to_numeric(clean[label_col], errors="coerce")
        clean = clean.dropna()
        if len(clean) < int(min_assets):
            continue
        ic = _spearman(clean[score_col], clean[label_col])
        if np.isfinite(ic):
            rows.append({"date": pd.Timestamp(day), "assets": int(len(clean)), "rank_ic": float(ic)})
    return pd.DataFrame(rows)


def daily_quantile_spread(panel: pd.DataFrame, score_col: str = "score", label_col: str = "forward_opportunity_pct", quantiles: int = 5, min_assets: int = 5) -> pd.DataFrame:
    if panel is None or panel.empty:
        return pd.DataFrame(columns=["date", "assets", "bottom_mean", "top_mean", "top_bottom_spread"])
    q = max(2, int(quantiles))
    rows: list[dict] = []
    x = panel.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    for day, group in x.groupby("date", sort=True):
        clean = group[[score_col, label_col]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(clean) < max(int(min_assets), q) or clean[score_col].nunique() < q:
            continue
        ranks = clean[score_col].rank(method="first")
        bucket = pd.qcut(ranks, q=q, labels=False, duplicates="drop")
        if bucket.nunique() < 2:
            continue
        clean = clean.assign(bucket=bucket)
        means = clean.groupby("bucket")[label_col].mean()
        bottom, top = float(means.iloc[0]), float(means.iloc[-1])
        rows.append({"date": pd.Timestamp(day), "assets": int(len(clean)), "bottom_mean": bottom, "top_mean": top, "top_bottom_spread": top - bottom})
    return pd.DataFrame(rows)


def cross_sectional_quantile_summary(panel: pd.DataFrame, score_col: str = "score", label_col: str = "forward_opportunity_pct", quantiles: int = 5, min_assets: int = 5) -> pd.DataFrame:
    if panel is None or panel.empty:
        return pd.DataFrame(columns=["quantile", "observations", "dates", "avg_score", "avg_forward_opportunity_pct"])
    q = max(2, int(quantiles))
    parts: list[pd.DataFrame] = []
    x = panel.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    for _, group in x.groupby("date", sort=True):
        clean = group[["date", "code", score_col, label_col]].copy()
        clean[score_col] = pd.to_numeric(clean[score_col], errors="coerce")
        clean[label_col] = pd.to_numeric(clean[label_col], errors="coerce")
        clean = clean.dropna()
        if len(clean) < max(int(min_assets), q) or clean[score_col].nunique() < q:
            continue
        clean["quantile"] = pd.qcut(clean[score_col].rank(method="first"), q=q, labels=False, duplicates="drop") + 1
        parts.append(clean)
    if not parts:
        return pd.DataFrame(columns=["quantile", "observations", "dates", "avg_score", "avg_forward_opportunity_pct"])
    z = pd.concat(parts, ignore_index=True)
    out = z.groupby("quantile", as_index=False).agg(observations=("code", "size"), dates=("date", "nunique"), avg_score=(score_col, "mean"), avg_forward_opportunity_pct=(label_col, "mean"))
    out["avg_score"] = out["avg_score"].round(4)
    out["avg_forward_opportunity_pct"] = out["avg_forward_opportunity_pct"].round(4)
    return out.sort_values("quantile").reset_index(drop=True)


def summarize_cross_sectional(panel: pd.DataFrame, score_col: str = "score", label_col: str = "forward_opportunity_pct", quantiles: int = 5, min_assets: int = 5) -> CrossSectionalSummary:
    ic = daily_cross_sectional_ic(panel, score_col, label_col, min_assets)
    spread = daily_quantile_spread(panel, score_col, label_col, quantiles, min_assets)
    vals = pd.to_numeric(ic.get("rank_ic"), errors="coerce").dropna() if not ic.empty else pd.Series(dtype=float)
    std = float(vals.std(ddof=0)) if not vals.empty else float("nan")
    mean = float(vals.mean()) if not vals.empty else float("nan")
    ir = mean / std if np.isfinite(std) and std > 0 else float("nan")
    return CrossSectionalSummary(int(len(vals)), int(panel[["date", "code"]].dropna().drop_duplicates().shape[0]) if panel is not None and not panel.empty else 0, round(mean, 4) if np.isfinite(mean) else float("nan"), round(float(vals.median()), 4) if not vals.empty else float("nan"), round(float((vals > 0).mean() * 100), 2) if not vals.empty else 0.0, round(std, 4) if np.isfinite(std) else float("nan"), round(ir, 4) if np.isfinite(ir) else float("nan"), round(float(spread["top_bottom_spread"].mean()), 4) if not spread.empty else float("nan"))


def _date_folds(unique_dates: pd.DatetimeIndex, min_train_dates: int, test_dates: int, gap_dates: int) -> list[tuple[int, np.ndarray, np.ndarray]]:
    dates = np.asarray(unique_dates, dtype="datetime64[ns]")
    min_train_dates, test_dates, gap_dates = int(min_train_dates), int(test_dates), int(gap_dates)
    if min_train_dates <= 0 or test_dates <= 0 or gap_dates < 0:
        raise ValueError("invalid date split sizes")
    rows: list[tuple[int, np.ndarray, np.ndarray]] = []
    train_end, fold = min_train_dates, 1
    while train_end + gap_dates < len(dates):
        test_start = train_end + gap_dates
        test_end = min(len(dates), test_start + test_dates)
        if test_start >= test_end:
            break
        rows.append((fold, dates[:train_end], dates[test_start:test_end]))
        train_end, fold = test_end, fold + 1
    return rows


def _prepare_numeric_groups(frame: pd.DataFrame, label_col: str, min_assets: int) -> list[tuple[np.ndarray, np.ndarray]]:
    groups: list[tuple[np.ndarray, np.ndarray]] = []
    needed = [*COMPONENT_COLUMNS, label_col]
    for _, day in frame.groupby("date", sort=False):
        clean = day[needed].apply(pd.to_numeric, errors="coerce").dropna()
        if len(clean) < int(min_assets):
            continue
        features = clean[list(COMPONENT_COLUMNS)].to_numpy(dtype=float)
        label_values = clean[label_col].to_numpy(dtype=float)
        label_ranks = _average_ranks(label_values)
        if np.allclose(label_ranks, label_ranks[0]):
            continue
        groups.append((features, label_ranks))
    return groups


def _mean_group_ic(groups: list[tuple[np.ndarray, np.ndarray]], weights: np.ndarray) -> float:
    values: list[float] = []
    for features, label_ranks in groups:
        score_ranks = _average_ranks(features @ weights)
        ic = _rank_corr_from_ranks(score_ranks, label_ranks)
        if np.isfinite(ic):
            values.append(ic)
    return float(np.mean(values)) if values else float("nan")


def optimize_cross_sectional_weights_on_training(train_panel: pd.DataFrame, label_col: str = "forward_opportunity_pct", n_candidates: int = 256, seed: int = 42, max_weight: float = 0.55, min_assets: int = 5, inner_min_train_dates: int | None = None, inner_test_dates: int = 20, gap_dates: int = 5, stability_penalty: float = 0.20) -> dict[str, object]:
    missing = [c for c in ["date", "code", label_col, *COMPONENT_COLUMNS] if c not in train_panel.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    x = train_panel.copy().dropna(subset=[label_col, *COMPONENT_COLUMNS])
    x["date"] = pd.to_datetime(x["date"]).dt.normalize()
    dates = pd.DatetimeIndex(sorted(x["date"].unique()))
    if len(dates) < 8:
        raise ValueError("not enough training dates")
    if inner_min_train_dates is None:
        inner_min_train_dates = max(5, min(60, len(dates) // 2))
    candidates = generate_weight_candidates(n_candidates, seed, max_weight)
    inner = _date_folds(dates, inner_min_train_dates, inner_test_dates, gap_dates)
    inner_groups: list[list[tuple[np.ndarray, np.ndarray]]] = []
    for _, _, test_dates_arr in inner:
        test = x[x["date"].isin(pd.to_datetime(test_dates_arr))]
        groups = _prepare_numeric_groups(test, label_col, min_assets)
        if groups:
            inner_groups.append(groups)
    all_groups = _prepare_numeric_groups(x, label_col, min_assets)
    best: dict[str, object] | None = None
    for weights in candidates:
        w = np.asarray([weights[c] for c in COMPONENT_COLUMNS], dtype=float)
        fold_ics = [_mean_group_ic(groups, w) for groups in inner_groups]
        fold_ics = [v for v in fold_ics if np.isfinite(v)]
        if fold_ics:
            mean_ic = float(np.mean(fold_ics))
            std_ic = float(np.std(fold_ics, ddof=0))
            objective = mean_ic - float(stability_penalty) * std_ic
            used_inner = True
        else:
            mean_ic = _mean_group_ic(all_groups, w)
            mean_ic = mean_ic if np.isfinite(mean_ic) else -1.0
            std_ic = 0.0
            objective = mean_ic
            used_inner = False
        row = {"weights": normalize_weights(weights), "objective": objective, "mean_inner_ic": mean_ic, "std_inner_ic": std_ic, "valid_inner_folds": len(fold_ics), "used_inner_cv": used_inner, "candidates_evaluated": len(candidates)}
        if best is None or float(row["objective"]) > float(best["objective"]) + 1e-12:
            best = row
    assert best is not None
    return best


def assess_cross_sectional_promotion_gate(metrics: pd.DataFrame, min_positive_fold_rate: float = 60.0, min_ic_improvement: float = 0.03, max_median_random_pvalue: float = 0.20, require_positive_spread: bool = True) -> dict[str, object]:
    if metrics is None or metrics.empty:
        return {"promote": False, "reason": "no cross-sectional OOS folds"}
    opt = pd.to_numeric(metrics.get("optimized_mean_daily_ic"), errors="coerce").dropna()
    if opt.empty:
        return {"promote": False, "reason": "no valid optimized cross-sectional IC"}
    baseline_cols = ["v01_mean_daily_ic", "amplitude_mean_daily_ic", "liquidity_mean_daily_ic", "tradable_space_mean_daily_ic"]
    baseline_means: dict[str, float] = {}
    for col in baseline_cols:
        values = pd.to_numeric(metrics.get(col), errors="coerce").dropna()
        if not values.empty:
            baseline_means[col] = float(values.mean())
    strongest = max(baseline_means.values()) if baseline_means else float("-inf")
    opt_mean = float(opt.mean())
    positive_rate = float((opt > 0).mean() * 100)
    pvals = pd.to_numeric(metrics.get("optimized_random_pvalue"), errors="coerce").dropna()
    median_p = float(pvals.median()) if not pvals.empty else 1.0
    spreads = pd.to_numeric(metrics.get("optimized_top_bottom_spread"), errors="coerce").dropna()
    mean_spread = float(spreads.mean()) if not spreads.empty else float("nan")
    checks = {"beats_strongest_baseline": opt_mean >= strongest + float(min_ic_improvement), "positive_fold_rate": positive_rate >= float(min_positive_fold_rate), "random_null_significance": median_p <= float(max_median_random_pvalue), "positive_top_bottom_spread": (mean_spread > 0) if require_positive_spread else True}
    promote = all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    return {"promote": promote, "reason": "all cross-sectional OOS gates passed" if promote else "failed: " + ", ".join(failed), "optimized_mean_daily_ic": round(opt_mean, 4), "strongest_baseline_mean_daily_ic": round(strongest, 4) if np.isfinite(strongest) else np.nan, "positive_fold_rate_pct": round(positive_rate, 2), "median_random_pvalue": round(median_p, 4), "mean_top_bottom_spread": round(mean_spread, 4) if np.isfinite(mean_spread) else np.nan, **checks}


def _random_null_means(test: pd.DataFrame, label_col: str, min_assets: int, trials: int, rng: np.random.Generator) -> np.ndarray:
    label_groups: list[np.ndarray] = []
    for _, day in test.groupby("date", sort=False):
        labels = pd.to_numeric(day[label_col], errors="coerce").dropna().to_numpy(dtype=float)
        if len(labels) >= int(min_assets) and len(np.unique(labels)) >= 2:
            label_groups.append(_average_ranks(labels))
    values: list[float] = []
    for _ in range(max(1, int(trials))):
        ics: list[float] = []
        for label_ranks in label_groups:
            score_ranks = _average_ranks(rng.normal(size=len(label_ranks)))
            ic = _rank_corr_from_ranks(score_ranks, label_ranks)
            if np.isfinite(ic):
                ics.append(ic)
        if ics:
            values.append(float(np.mean(ics)))
    return np.asarray(values, dtype=float)


def walk_forward_cross_sectional_optimize(panel: pd.DataFrame, min_train_dates: int = 60, test_dates: int = 20, gap_dates: int = 5, label_col: str = "forward_opportunity_pct", min_assets: int = 5, n_candidates: int = 256, seed: int = 42, max_weight: float = 0.55, random_trials: int = 200) -> tuple[pd.DataFrame, pd.DataFrame]:
    x = panel.copy().dropna(subset=["date", "code", label_col, *COMPONENT_COLUMNS])
    x["date"] = pd.to_datetime(x["date"]).dt.normalize()
    dates = pd.DatetimeIndex(sorted(x["date"].unique()))
    folds = _date_folds(dates, min_train_dates, test_dates, gap_dates)
    metrics: list[dict] = []
    weights_rows: list[dict] = []
    for fold, train_dates_arr, test_dates_arr in folds:
        train_dates_idx = pd.to_datetime(train_dates_arr)
        test_dates_idx = pd.to_datetime(test_dates_arr)
        train = x[x["date"].isin(train_dates_idx)].copy()
        test = x[x["date"].isin(test_dates_idx)].copy()
        opt = optimize_cross_sectional_weights_on_training(train, label_col, n_candidates, seed + fold, max_weight, min_assets, gap_dates=gap_dates)
        test = apply_weights(test, opt["weights"], output_col="optimized_score")
        model_cols = {"optimized": "optimized_score", "v01": "score", "amplitude": "amplitude_score", "liquidity": "liquidity_score", "tradable_space": "tradable_space_score"}
        model_means: dict[str, float] = {}
        for name, col in model_cols.items():
            if col not in test.columns:
                model_means[name] = float("nan")
                continue
            ic = daily_cross_sectional_ic(test, col, label_col, min_assets)
            model_means[name] = float(ic["rank_ic"].mean()) if not ic.empty else float("nan")
        rng = np.random.default_rng(seed + 10000 + fold)
        null = _random_null_means(test, label_col, min_assets, random_trials, rng)
        optimized = model_means["optimized"]
        percentile = float((null <= optimized).mean() * 100) if len(null) and np.isfinite(optimized) else float("nan")
        pvalue = float((1 + np.sum(null >= optimized)) / (len(null) + 1)) if len(null) and np.isfinite(optimized) else float("nan")
        spread = daily_quantile_spread(test, "optimized_score", label_col, quantiles=5, min_assets=min_assets)
        metrics.append({"fold": fold, "train_start_date": str(pd.Timestamp(train_dates_arr[0]).date()), "train_end_date": str(pd.Timestamp(train_dates_arr[-1]).date()), "test_start_date": str(pd.Timestamp(test_dates_arr[0]).date()), "test_end_date": str(pd.Timestamp(test_dates_arr[-1]).date()), "train_dates": len(train_dates_arr), "test_dates": len(test_dates_arr), "optimized_mean_daily_ic": round(optimized, 4) if np.isfinite(optimized) else np.nan, "v01_mean_daily_ic": round(model_means["v01"], 4) if np.isfinite(model_means["v01"]) else np.nan, "amplitude_mean_daily_ic": round(model_means["amplitude"], 4) if np.isfinite(model_means["amplitude"]) else np.nan, "liquidity_mean_daily_ic": round(model_means["liquidity"], 4) if np.isfinite(model_means["liquidity"]) else np.nan, "tradable_space_mean_daily_ic": round(model_means["tradable_space"], 4) if np.isfinite(model_means["tradable_space"]) else np.nan, "random_mean_daily_ic": round(float(null.mean()), 4) if len(null) else np.nan, "random_std_daily_ic": round(float(null.std(ddof=0)), 4) if len(null) else np.nan, "optimized_random_percentile": round(percentile, 2) if np.isfinite(percentile) else np.nan, "optimized_random_pvalue": round(pvalue, 4) if np.isfinite(pvalue) else np.nan, "optimized_top_bottom_spread": round(float(spread["top_bottom_spread"].mean()), 4) if not spread.empty else np.nan})
        weights_rows.append({"fold": fold, **{f"w_{k}": round(float(v), 6) for k, v in opt["weights"].items()}})
    return pd.DataFrame(metrics), pd.DataFrame(weights_rows)
