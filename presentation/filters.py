from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class ScoreFilterConfig:
    """User-editable post-score screening rules.

    Every non-None rule is combined with logical AND. Values use the same
    units as the score table: percentages for amplitude/intraday/drawdown,
    CNY for amount, and yuan for latest_close.
    """

    min_score: float | None = None
    max_score: float | None = None
    min_avg_amplitude: float | None = None
    max_avg_amplitude: float | None = None
    min_intraday_space: float | None = None
    max_intraday_space: float | None = None
    min_median_amount: float | None = None
    max_abs_drawdown: float | None = None
    min_latest_close: float | None = None
    max_latest_close: float | None = None
    min_observations: int | None = None
    min_amplitude_score: float | None = None
    min_liquidity_score: float | None = None
    min_tradable_space_score: float | None = None
    min_mean_reversion_score: float | None = None
    min_trend_score: float | None = None
    min_risk_score: float | None = None
    grades: tuple[str, ...] | None = None
    exclude_errors: bool = True


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        raise KeyError(f"score table missing required filter column: {column}")
    return pd.to_numeric(frame[column], errors="coerce")


def apply_score_filters(
    scores: pd.DataFrame,
    config: ScoreFilterConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply enabled rules and return (filtered_rows, per-rule audit report)."""

    if scores is None or scores.empty:
        return pd.DataFrame() if scores is None else scores.copy(), pd.DataFrame(
            columns=["rule", "before", "after", "removed"]
        )

    out = scores.copy()
    report: list[dict[str, object]] = []

    def apply(rule: str, mask: pd.Series) -> None:
        nonlocal out
        before = len(out)
        aligned = mask.reindex(out.index).fillna(False).astype(bool)
        out = out.loc[aligned].copy()
        after = len(out)
        report.append({"rule": rule, "before": before, "after": after, "removed": before - after})

    if config.exclude_errors and "error" in out.columns:
        apply("排除数据错误", out["error"].fillna("").astype(str).eq(""))

    rules: list[tuple[str, str, str, float | int | None]] = [
        ("T Score下限", "score", ">=", config.min_score),
        ("T Score上限", "score", "<=", config.max_score),
        ("平均振幅下限", "avg_amplitude", ">=", config.min_avg_amplitude),
        ("平均振幅上限", "avg_amplitude", "<=", config.max_avg_amplitude),
        ("日内空间下限", "avg_intraday_space", ">=", config.min_intraday_space),
        ("日内空间上限", "avg_intraday_space", "<=", config.max_intraday_space),
        ("中位成交额下限", "median_amount", ">=", config.min_median_amount),
        ("最新价下限", "latest_close", ">=", config.min_latest_close),
        ("最新价上限", "latest_close", "<=", config.max_latest_close),
        ("有效样本天数下限", "observations", ">=", config.min_observations),
        ("振幅子评分下限", "amplitude_score", ">=", config.min_amplitude_score),
        ("流动性子评分下限", "liquidity_score", ">=", config.min_liquidity_score),
        ("可交易空间子评分下限", "tradable_space_score", ">=", config.min_tradable_space_score),
        ("均值回归子评分下限", "mean_reversion_score", ">=", config.min_mean_reversion_score),
        ("趋势稳定子评分下限", "trend_score", ">=", config.min_trend_score),
        ("风险控制子评分下限", "risk_score", ">=", config.min_risk_score),
    ]

    for label, column, op, value in rules:
        if value is None:
            continue
        values = _numeric(out, column)
        if op == ">=":
            apply(f"{label} ≥ {value}", values >= float(value))
        else:
            apply(f"{label} ≤ {value}", values <= float(value))

    if config.max_abs_drawdown is not None:
        drawdown = _numeric(out, "max_drawdown").abs()
        apply(f"最大回撤绝对值 ≤ {config.max_abs_drawdown}%", drawdown <= float(config.max_abs_drawdown))

    if config.grades:
        if "grade" not in out.columns:
            raise KeyError("score table missing required filter column: grade")
        allowed = tuple(str(x) for x in config.grades)
        apply("等级 ∈ " + "/".join(allowed), out["grade"].astype(str).isin(allowed))

    out = out.reset_index(drop=True)
    audit = pd.DataFrame(report, columns=["rule", "before", "after", "removed"])
    return out, audit
