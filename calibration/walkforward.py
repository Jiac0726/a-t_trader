from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from backtest.t_engine import CostModel, best_single_t_envelope
from features.daily import daily_features
from scoring.t_score import build_t_score


@dataclass(slots=True)
class WalkForwardFold:
    fold: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    def to_dict(self) -> dict:
        return asdict(self)


def expanding_walk_forward(
    n_samples: int,
    min_train_size: int,
    test_size: int,
    gap: int = 0,
) -> list[WalkForwardFold]:
    """Expanding-window time-series splits with an explicit pre-test gap.

    Semantics mirror mature time-series CV practice: every train observation is
    earlier than every test observation; the gap can purge rows whose labels
    overlap the test horizon. No shuffling is ever performed.
    """
    n_samples = int(n_samples)
    min_train_size = int(min_train_size)
    test_size = int(test_size)
    gap = int(gap)
    if n_samples <= 0 or min_train_size <= 0 or test_size <= 0 or gap < 0:
        raise ValueError("invalid walk-forward sizes")

    folds: list[WalkForwardFold] = []
    train_end_exclusive = min_train_size
    fold_no = 1
    while train_end_exclusive + gap < n_samples:
        test_start = train_end_exclusive + gap
        test_end_exclusive = min(n_samples, test_start + test_size)
        if test_start >= test_end_exclusive:
            break
        folds.append(
            WalkForwardFold(
                fold=fold_no,
                train_start=0,
                train_end=train_end_exclusive - 1,
                test_start=test_start,
                test_end=test_end_exclusive - 1,
            )
        )
        fold_no += 1
        train_end_exclusive = test_end_exclusive
    return folds


def build_score_history(
    code: str,
    daily_df: pd.DataFrame,
    lookback: int = 60,
    name: str = "",
    provider: str = "",
) -> pd.DataFrame:
    """Recompute T Score as it would have been known at each historical date.

    For date D, only daily bars at or before D are passed into the feature layer.
    This deliberately trades speed for a transparent no-look-ahead baseline.
    """
    x = daily_df.copy().sort_values("datetime").reset_index(drop=True)
    x["datetime"] = pd.to_datetime(x["datetime"])
    if len(x) < lookback:
        return pd.DataFrame()

    rows: list[dict] = []
    for end_i in range(lookback - 1, len(x)):
        history = x.iloc[: end_i + 1]
        features = daily_features(history, lookback=lookback)
        result = build_t_score(code, name, features, provider=provider)
        row = result.to_dict()
        row["date"] = x.iloc[end_i]["datetime"].normalize()
        rows.append(row)
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def build_opportunity_history(
    intraday_df: pd.DataFrame,
    mode: str = "positive",
    bottom_shares: int = 1000,
    t_ratio: float = 0.5,
    costs: CostModel | None = None,
) -> pd.DataFrame:
    """Build one hindsight opportunity-ceiling label per intraday trading day.

    These labels are explicitly NOT strategy returns. They measure whether a
    future day contained an ordered price move large enough to overcome costs.
    """
    trades = best_single_t_envelope(
        intraday_df,
        mode=mode,
        bottom_shares=bottom_shares,
        t_ratio=t_ratio,
        costs=costs or CostModel(),
    )
    if trades.empty:
        return pd.DataFrame(columns=["date", "opportunity_net_return_pct"])
    out = trades[["date", "net_return_pct"]].copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.rename(columns={"net_return_pct": "opportunity_net_return_pct"})
    return out.sort_values("date").reset_index(drop=True)


def attach_forward_labels(
    score_history: pd.DataFrame,
    opportunity_history: pd.DataFrame,
    horizon: int = 5,
    min_future_days: int | None = None,
) -> pd.DataFrame:
    """Attach mean opportunity from the next N *strictly future* trading days."""
    horizon = int(horizon)
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    min_future_days = horizon if min_future_days is None else int(min_future_days)
    if min_future_days <= 0 or min_future_days > horizon:
        raise ValueError("min_future_days must be in 1..horizon")

    scores = score_history.copy()
    opp = opportunity_history.copy()
    if scores.empty or opp.empty:
        scores["forward_opportunity_pct"] = np.nan
        scores["forward_days"] = 0
        return scores

    scores["date"] = pd.to_datetime(scores["date"])
    opp["date"] = pd.to_datetime(opp["date"])
    opp = opp.sort_values("date").reset_index(drop=True)
    opp_dates = opp["date"].to_numpy(dtype="datetime64[ns]")
    opp_values = pd.to_numeric(opp["opportunity_net_return_pct"], errors="coerce").to_numpy(dtype=float)

    labels: list[float] = []
    counts: list[int] = []
    for score_date in scores["date"]:
        # side='right' is the leakage guard: same-day opportunity is never a label.
        start = int(np.searchsorted(opp_dates, np.datetime64(score_date), side="right"))
        future = opp_values[start : start + horizon]
        future = future[np.isfinite(future)]
        counts.append(int(len(future)))
        labels.append(float(np.mean(future)) if len(future) >= min_future_days else np.nan)

    scores["forward_opportunity_pct"] = labels
    scores["forward_days"] = counts
    return scores


def rank_ic(frame: pd.DataFrame, score_col: str = "score", label_col: str = "forward_opportunity_pct") -> float:
    x = frame[[score_col, label_col]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(x) < 3 or x[score_col].nunique() < 2 or x[label_col].nunique() < 2:
        return float("nan")
    return float(x[score_col].rank(method="average").corr(x[label_col].rank(method="average")))


def score_bucket_summary(
    frame: pd.DataFrame,
    buckets: int = 5,
    score_col: str = "score",
    label_col: str = "forward_opportunity_pct",
) -> pd.DataFrame:
    x = frame[[score_col, label_col]].copy()
    x[score_col] = pd.to_numeric(x[score_col], errors="coerce")
    x[label_col] = pd.to_numeric(x[label_col], errors="coerce")
    x = x.dropna()
    if x.empty:
        return pd.DataFrame(columns=["bucket", "count", "avg_score", "avg_forward_opportunity_pct"])
    q = min(max(2, int(buckets)), int(x[score_col].nunique()))
    if q < 2:
        return pd.DataFrame(columns=["bucket", "count", "avg_score", "avg_forward_opportunity_pct"])
    x["bucket"] = pd.qcut(x[score_col].rank(method="first"), q=q, labels=False, duplicates="drop") + 1
    out = (
        x.groupby("bucket", as_index=False)
        .agg(
            count=(score_col, "size"),
            avg_score=(score_col, "mean"),
            avg_forward_opportunity_pct=(label_col, "mean"),
        )
        .sort_values("bucket")
    )
    out["avg_score"] = out["avg_score"].round(3)
    out["avg_forward_opportunity_pct"] = out["avg_forward_opportunity_pct"].round(4)
    return out


def evaluate_score_walk_forward(
    labeled: pd.DataFrame,
    min_train_size: int = 60,
    test_size: int = 20,
    gap: int = 5,
    score_col: str = "score",
    label_col: str = "forward_opportunity_pct",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate a fixed T Score out-of-sample over chronological test folds.

    This stage does not optimize weights yet. It answers a prerequisite question:
    does the current score rank future realized opportunity at all, out of sample?
    """
    x = labeled.copy().sort_values("date").dropna(subset=[score_col, label_col]).reset_index(drop=True)
    folds = expanding_walk_forward(len(x), min_train_size=min_train_size, test_size=test_size, gap=gap)
    rows: list[dict] = []
    test_parts: list[pd.DataFrame] = []
    for fold in folds:
        test = x.iloc[fold.test_start : fold.test_end + 1].copy()
        if test.empty:
            continue
        ic = rank_ic(test, score_col=score_col, label_col=label_col)
        rows.append(
            {
                "fold": fold.fold,
                "train_start_date": str(pd.Timestamp(x.iloc[fold.train_start]["date"]).date()),
                "train_end_date": str(pd.Timestamp(x.iloc[fold.train_end]["date"]).date()),
                "test_start_date": str(pd.Timestamp(test.iloc[0]["date"]).date()),
                "test_end_date": str(pd.Timestamp(test.iloc[-1]["date"]).date()),
                "test_rows": len(test),
                "rank_ic": round(ic, 4) if np.isfinite(ic) else np.nan,
                "avg_score": round(float(test[score_col].mean()), 3),
                "avg_forward_opportunity_pct": round(float(test[label_col].mean()), 4),
            }
        )
        test["fold"] = fold.fold
        test_parts.append(test)
    metrics = pd.DataFrame(rows)
    oos = pd.concat(test_parts, ignore_index=True) if test_parts else pd.DataFrame()
    return metrics, oos
