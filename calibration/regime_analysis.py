from __future__ import annotations

import pandas as pd

from calibration.cross_sectional import summarize_cross_sectional


def summarize_cross_sectional_by_regime(
    panel: pd.DataFrame,
    *,
    regime_col: str = "market_regime",
    score_cols: tuple[str, ...] = ("score",),
    label_col: str = "forward_opportunity_pct",
    min_assets: int = 5,
) -> pd.DataFrame:
    """Evaluate cross-sectional ranking quality separately by market regime."""
    if panel is None or panel.empty:
        return pd.DataFrame()
    if regime_col not in panel.columns:
        raise ValueError(f"missing regime column: {regime_col}")
    rows: list[dict] = []
    for regime, subset in panel.dropna(subset=[regime_col]).groupby(regime_col, sort=True):
        for score_col in score_cols:
            if score_col not in subset.columns:
                continue
            summary = summarize_cross_sectional(subset, score_col=score_col, label_col=label_col, min_assets=min_assets)
            rows.append({"regime": str(regime), "score_model": score_col, **summary.to_dict()})
    return pd.DataFrame(rows).sort_values(["regime", "score_model"]).reset_index(drop=True) if rows else pd.DataFrame()


def regime_coverage(panel: pd.DataFrame, regime_col: str = "market_regime") -> pd.DataFrame:
    if panel is None or panel.empty or regime_col not in panel.columns:
        return pd.DataFrame(columns=["regime", "dates", "observations", "share_pct"])
    x = panel.dropna(subset=[regime_col]).copy()
    total = len(x)
    out = x.groupby(regime_col, as_index=False).agg(dates=("date", "nunique"), observations=(regime_col, "size")).rename(columns={regime_col: "regime"})
    out["share_pct"] = (out["observations"] / total * 100).round(2) if total else 0.0
    return out.sort_values("observations", ascending=False).reset_index(drop=True)
