from __future__ import annotations

import math
from core.models import TScoreResult


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def amplitude_score(avg_amp: float) -> float:
    # Highest score near 4.5%; low amplitude lacks room, very high amplitude gets risk penalty.
    if avg_amp <= 1.0:
        return 15 * avg_amp
    if avg_amp <= 2.5:
        return 15 + (avg_amp - 1.0) / 1.5 * 50
    if avg_amp <= 5.5:
        return 65 + (avg_amp - 2.5) / 3.0 * 35
    if avg_amp <= 8.0:
        return 100 - (avg_amp - 5.5) / 2.5 * 25
    return max(20, 75 - (avg_amp - 8.0) * 8)


def liquidity_score(median_amount: float) -> float:
    # Amount is CNY. 0.5bn starts becoming tradable; >=5bn saturates.
    if median_amount <= 0:
        return 0
    log_billion = math.log10(max(median_amount / 1e8, 0.01))
    return _clamp(25 + 35 * log_billion)


def tradable_space_score(space_pct: float) -> float:
    return _clamp(space_pct / 5.0 * 100)


def mean_reversion_score(reversal_rate: float) -> float:
    # Around 50-65% alternating daily direction is useful; extremes are not blindly rewarded.
    return _clamp(100 - abs(reversal_rate - 57.5) * 2.2)


def trend_score(trend_distance: float, drawdown: float) -> float:
    distance_part = _clamp(100 - max(0, trend_distance - 1.5) * 14)
    dd_part = _clamp(100 - max(0, abs(drawdown) - 8) * 3)
    return 0.6 * distance_part + 0.4 * dd_part


def risk_score(extreme_day_rate: float, volatility: float, drawdown: float) -> float:
    penalty = extreme_day_rate * 2.5 + max(0, volatility - 45) * 0.8 + max(0, abs(drawdown) - 15) * 1.5
    return _clamp(100 - penalty)


def grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 75:
        return "B+"
    if score >= 65:
        return "B"
    if score >= 55:
        return "C+"
    return "C"


def build_t_score(code: str, name: str, features: dict[str, float], provider: str = "") -> TScoreResult:
    a = amplitude_score(features["avg_amplitude"])
    l = liquidity_score(features["median_amount"])
    s = tradable_space_score(features["avg_intraday_space"])
    m = mean_reversion_score(features["reversal_rate"])
    t = trend_score(features["trend_distance"], features["max_drawdown"])
    r = risk_score(features["extreme_day_rate"], features["return_volatility"], features["max_drawdown"])
    total = 0.25 * a + 0.20 * l + 0.20 * s + 0.15 * m + 0.10 * t + 0.10 * r
    total = round(_clamp(total), 2)
    return TScoreResult(
        code=str(code).zfill(6),
        name=name,
        score=total,
        grade=grade(total),
        amplitude_score=round(a, 2),
        liquidity_score=round(l, 2),
        tradable_space_score=round(s, 2),
        mean_reversion_score=round(m, 2),
        trend_score=round(t, 2),
        risk_score=round(r, 2),
        avg_amplitude=round(features["avg_amplitude"], 3),
        median_amount=round(features["median_amount"], 2),
        avg_intraday_space=round(features["avg_intraday_space"], 3),
        max_drawdown=round(features["max_drawdown"], 3),
        latest_close=round(features["latest_close"], 3),
        observations=int(features["observations"]),
        provider=provider,
    )
