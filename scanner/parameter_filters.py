from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SpotFilterConfig:
    markets: tuple[str, ...] = ("SH", "SZ", "BJ")
    exclude_st: bool = True
    min_amount_yi: float = 0.0
    min_turnover: float = 0.0
    min_amplitude: float = 0.0
    max_amplitude: float = 100.0
    min_pct_change: float = -100.0
    max_pct_change: float = 100.0
    min_price: float = 0.0
    max_price: float = 100000.0


@dataclass(frozen=True)
class ScoreFilterConfig:
    min_score: float = 0.0
    min_avg_amplitude: float = 0.0
    max_avg_amplitude: float = 100.0
    min_intraday_space: float = 0.0
    min_median_amount_yi: float = 0.0
    max_drawdown_abs: float = 100.0
    allowed_risks: tuple[str, ...] = ("较低", "中等", "较高")


def apply_spot_filters(universe: pd.DataFrame, quotes: pd.DataFrame, cfg: SpotFilterConfig) -> pd.DataFrame:
    if universe is None or universe.empty:
        return pd.DataFrame()
    base = universe.copy()
    base["code"] = base["code"].astype(str).str.zfill(6)
    if "market" in base.columns:
        base = base[base["market"].astype(str).isin(cfg.markets)]
    if cfg.exclude_st and "name" in base.columns:
        base = base[~base["name"].astype(str).str.upper().str.contains("ST", na=False)]
    if base.empty:
        return base

    if quotes is None or quotes.empty:
        raise ValueError("实时筛选指标为空，无法执行主动参数筛选。")
    q = quotes.copy()
    q["code"] = q["code"].astype(str).str.zfill(6)
    merge_keys = ["code"]
    if "market" in base.columns and "market" in q.columns:
        merge_keys = ["market", "code"]
    out = base.merge(q, on=merge_keys, how="inner", suffixes=("", "_quote"))
    if out.empty:
        return out

    for col in ["amount", "turnover", "amplitude", "pct_change", "price"]:
        out[col] = pd.to_numeric(out.get(col), errors="coerce")

    out = out[
        out["amount"].ge(float(cfg.min_amount_yi) * 1e8)
        & out["turnover"].ge(float(cfg.min_turnover))
        & out["amplitude"].between(float(cfg.min_amplitude), float(cfg.max_amplitude), inclusive="both")
        & out["pct_change"].between(float(cfg.min_pct_change), float(cfg.max_pct_change), inclusive="both")
        & out["price"].between(float(cfg.min_price), float(cfg.max_price), inclusive="both")
    ]
    return out.sort_values(["amount", "turnover", "amplitude"], ascending=[False, False, False]).reset_index(drop=True)


def apply_score_filters(ranking: pd.DataFrame, cfg: ScoreFilterConfig) -> pd.DataFrame:
    if ranking is None or ranking.empty:
        return pd.DataFrame()
    out = ranking.copy()
    numeric_cols = ["score", "avg_amplitude", "avg_intraday_space", "median_amount", "max_drawdown"]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out.get(col), errors="coerce")
    out = out[
        out["score"].ge(float(cfg.min_score))
        & out["avg_amplitude"].between(float(cfg.min_avg_amplitude), float(cfg.max_avg_amplitude), inclusive="both")
        & out["avg_intraday_space"].ge(float(cfg.min_intraday_space))
        & out["median_amount"].ge(float(cfg.min_median_amount_yi) * 1e8)
        & out["max_drawdown"].abs().le(float(cfg.max_drawdown_abs))
    ]
    if "risk_label" in out.columns and cfg.allowed_risks:
        out = out[out["risk_label"].astype(str).isin(cfg.allowed_risks)]
    return out.sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)
