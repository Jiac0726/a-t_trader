from __future__ import annotations

import pandas as pd


_COMPONENT_LABELS = {
    "amplitude_score": "振幅空间",
    "liquidity_score": "流动性",
    "tradable_space_score": "日内空间",
    "mean_reversion_score": "均值回归",
    "trend_score": "趋势稳定",
    "risk_score": "风险控制",
}


def _suitability(score: float, error: str = "") -> str:
    if error:
        return "数据异常"
    if score >= 80:
        return "优先观察"
    if score >= 70:
        return "适合做T"
    if score >= 60:
        return "一般"
    return "不优先"


def _risk_label(row: pd.Series) -> str:
    if str(row.get("error", "") or "").strip():
        return "数据异常"
    risk_score = float(pd.to_numeric(row.get("risk_score", 0), errors="coerce") or 0)
    drawdown = abs(float(pd.to_numeric(row.get("max_drawdown", 0), errors="coerce") or 0))
    amplitude = float(pd.to_numeric(row.get("avg_amplitude", 0), errors="coerce") or 0)
    if risk_score < 45 or drawdown >= 25 or amplitude >= 9:
        return "较高"
    if risk_score < 70 or drawdown >= 15 or amplitude >= 6.5:
        return "中等"
    return "较低"


def _reason(row: pd.Series) -> str:
    if str(row.get("error", "") or "").strip():
        return "行情数据读取失败"

    ranked: list[tuple[float, str]] = []
    for col, label in _COMPONENT_LABELS.items():
        value = pd.to_numeric(row.get(col), errors="coerce")
        if pd.notna(value):
            ranked.append((float(value), label))
    ranked.sort(reverse=True)
    strengths = [label for score, label in ranked if score >= 65][:3]
    if not strengths:
        strengths = [label for _, label in ranked[:2]]

    amp = pd.to_numeric(row.get("avg_amplitude"), errors="coerce")
    amount = pd.to_numeric(row.get("median_amount"), errors="coerce")
    space = pd.to_numeric(row.get("avg_intraday_space"), errors="coerce")
    metrics: list[str] = []
    if pd.notna(amp):
        metrics.append(f"振幅{float(amp):.1f}%")
    if pd.notna(space):
        metrics.append(f"日内空间{float(space):.1f}%")
    if pd.notna(amount) and float(amount) > 0:
        metrics.append(f"中位成交额{float(amount) / 1e8:.1f}亿")

    prefix = "、".join(strengths)
    suffix = "｜".join(metrics[:3])
    return f"{prefix}；{suffix}" if prefix and suffix else (prefix or suffix or "暂无足够解释数据")


def enrich_t_ranking(scores: pd.DataFrame) -> pd.DataFrame:
    """Add a thin user-facing interpretation layer without changing T Score.

    The underlying quantitative score remains untouched. This function only
    adds rank, suitability, risk and concise explanation columns for the UI.
    """
    if scores is None or scores.empty:
        return pd.DataFrame()

    out = scores.copy()
    out["score"] = pd.to_numeric(out.get("score"), errors="coerce").fillna(0.0)
    out["median_amount"] = pd.to_numeric(out.get("median_amount"), errors="coerce").fillna(0.0)
    out = out.sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    out["suitability"] = [
        _suitability(float(row.get("score", 0)), str(row.get("error", "") or "")) for _, row in out.iterrows()
    ]
    out["risk_label"] = [_risk_label(row) for _, row in out.iterrows()]
    out["reason"] = [_reason(row) for _, row in out.iterrows()]
    return out
