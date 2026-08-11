from __future__ import annotations

from collections.abc import Mapping

import pandas as pd


def apply_active_filters(ranking: pd.DataFrame, filters: Mapping[str, object]) -> pd.DataFrame:
    """Apply only user-enabled screening constraints to a T-score ranking.

    Each filter is optional. Missing/None values mean the metric does not
    participate in screening. The score formula itself is not changed here.
    """
    if ranking is None or ranking.empty:
        return pd.DataFrame() if ranking is None else ranking.copy()

    out = ranking.copy()

    def num(column: str) -> pd.Series:
        if column not in out.columns:
            return pd.Series(float("nan"), index=out.index)
        return pd.to_numeric(out[column], errors="coerce")

    min_score = filters.get("min_score")
    if min_score is not None:
        out = out[num("score") >= float(min_score)]

    amp_range = filters.get("avg_amplitude_range")
    if amp_range is not None:
        lo, hi = amp_range
        values = num("avg_amplitude")
        out = out[values.between(float(lo), float(hi), inclusive="both")]

    min_amount = filters.get("min_median_amount")
    if min_amount is not None:
        out = out[num("median_amount") >= float(min_amount)]

    space_range = filters.get("intraday_space_range")
    if space_range is not None:
        lo, hi = space_range
        values = num("avg_intraday_space")
        out = out[values.between(float(lo), float(hi), inclusive="both")]

    drawdown_floor = filters.get("max_drawdown_floor")
    if drawdown_floor is not None:
        # max_drawdown is negative, e.g. -18% is better than -30%.
        out = out[num("max_drawdown") >= float(drawdown_floor)]

    metric_minimums = {
        "liquidity_score": "min_liquidity_score",
        "mean_reversion_score": "min_mean_reversion_score",
        "risk_score": "min_risk_score",
        "observations": "min_observations",
    }
    for column, key in metric_minimums.items():
        value = filters.get(key)
        if value is not None:
            out = out[num(column) >= float(value)]

    return out.reset_index(drop=True)


def active_filter_descriptions(filters: Mapping[str, object]) -> list[str]:
    descriptions: list[str] = []
    if filters.get("min_score") is not None:
        descriptions.append(f"T Score ≥ {float(filters['min_score']):g}")
    if filters.get("avg_amplitude_range") is not None:
        lo, hi = filters["avg_amplitude_range"]
        descriptions.append(f"平均振幅 {float(lo):g}%–{float(hi):g}%")
    if filters.get("min_median_amount") is not None:
        descriptions.append(f"中位成交额 ≥ {float(filters['min_median_amount']) / 1e8:g}亿")
    if filters.get("intraday_space_range") is not None:
        lo, hi = filters["intraday_space_range"]
        descriptions.append(f"日内空间 {float(lo):g}%–{float(hi):g}%")
    if filters.get("max_drawdown_floor") is not None:
        descriptions.append(f"最大回撤 ≥ {float(filters['max_drawdown_floor']):g}%")
    if filters.get("min_liquidity_score") is not None:
        descriptions.append(f"流动性得分 ≥ {float(filters['min_liquidity_score']):g}")
    if filters.get("min_mean_reversion_score") is not None:
        descriptions.append(f"均值回归得分 ≥ {float(filters['min_mean_reversion_score']):g}")
    if filters.get("min_risk_score") is not None:
        descriptions.append(f"风险控制得分 ≥ {float(filters['min_risk_score']):g}")
    if filters.get("min_observations") is not None:
        descriptions.append(f"历史样本 ≥ {int(filters['min_observations'])}日")
    return descriptions
