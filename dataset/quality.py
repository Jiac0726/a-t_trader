from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(slots=True)
class QualityGateSummary:
    rows: int
    labeled_rows: int
    eligible_rows: int
    excluded_rows: int
    eligible_codes: int
    exclusion_reasons: dict[str, int]
    daily_adjust: str
    minute_adjust: str

    def to_dict(self) -> dict:
        return asdict(self)


def assess_dataset_promotion_readiness(
    *,
    exact_snapshots: bool,
    eligible_rows: int,
    minute_selection_policy: str,
    full_existing_minute_coverage: bool = False,
) -> dict[str, object]:
    """Decide whether dataset construction itself is safe for formal OOS.

    Statistical promotion is evaluated later.  This gate only certifies that
    the dataset used exact point-in-time membership, contains eligible labels,
    and did not choose historical securities using a future/latest score.
    """
    policy = str(minute_selection_policy).strip().lower()
    unbiased = bool(
        policy == "stable-hash"
        or (policy == "existing-full-cache" and full_existing_minute_coverage)
    )
    ready = bool(exact_snapshots and int(eligible_rows) > 0 and unbiased)
    reasons: list[str] = []
    if not exact_snapshots:
        reasons.append("exact_point_in_time_snapshots_required")
    if int(eligible_rows) <= 0:
        reasons.append("no_eligible_rows")
    if not unbiased:
        reasons.append("minute_selection_may_use_future_information_or_partial_unknown_cache")
    return {
        "promotion_ready_dataset": ready,
        "unbiased_minute_selection": unbiased,
        "minute_selection_policy": policy,
        "full_existing_minute_coverage": bool(full_existing_minute_coverage),
        "dataset_gate_reasons": reasons,
    }


def _bool_series(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(default)
    text = values.fillna("").astype(str).str.strip().str.lower()
    return text.isin({"1", "true", "yes", "y", "t"})


def _minute_quality_by_code(coverage: pd.DataFrame, required_adjust: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if coverage is None or coverage.empty or "code" not in coverage.columns:
        return result
    for _, row in coverage.iterrows():
        code = str(row.get("code", "")).zfill(6)
        reasons: list[str] = []
        adjust = str(row.get("adjust", "") or "").strip().lower()
        if adjust != str(required_adjust).lower():
            reasons.append("minute_adjust_not_raw")
        rows = pd.to_numeric(pd.Series([row.get("rows_5m", 0)]), errors="coerce").fillna(0).iloc[0]
        if int(rows) <= 0:
            reasons.append("no_minute_rows")
        providers = str(row.get("providers", "") or "").strip().lower()
        if not providers or providers in {"unknown", "legacy-unknown"} or "legacy-unknown" in providers:
            reasons.append("minute_provider_unknown")
        result[code] = reasons
    return result


def apply_oos_quality_gate(
    panel: pd.DataFrame,
    minute_coverage: pd.DataFrame,
    *,
    required_daily_adjust: str = "qfq",
    required_minute_adjust: str = "none",
    reject_estimated_daily_amount: bool = True,
    reject_unknown_daily_lineage: bool = True,
    reject_unverified_bse_stitching: bool = True,
) -> tuple[pd.DataFrame, QualityGateSummary]:
    """Annotate labeled research rows without deleting evidence.

    The gate is deliberately conservative. It does not change scores or labels;
    it only marks whether a row is suitable for formal cross-sectional OOS work.
    Raw rows remain available for diagnostics and sensitivity analysis.
    """
    if panel is None:
        panel = pd.DataFrame()
    out = panel.copy()
    if out.empty:
        return out, QualityGateSummary(0, 0, 0, 0, 0, {}, required_daily_adjust, required_minute_adjust)

    out["code"] = out["code"].astype(str).str.zfill(6)
    minute_quality = _minute_quality_by_code(minute_coverage, required_minute_adjust)
    daily_unknown = _bool_series(out, "lineage_has_unknown")
    daily_estimated = _bool_series(out, "lineage_has_estimated_amount")
    daily_stitched = _bool_series(out, "lineage_bse_stitched")
    daily_adjust = out.get("lineage_adjust", pd.Series("unknown", index=out.index)).fillna("unknown").astype(str).str.lower()

    all_reasons: list[str] = []
    eligible: list[bool] = []
    counter: Counter[str] = Counter()

    for i, row in out.iterrows():
        reasons: list[str] = []
        if str(daily_adjust.loc[i]).strip() != str(required_daily_adjust).lower():
            reasons.append("daily_adjust_mismatch")
        if reject_unknown_daily_lineage and bool(daily_unknown.loc[i]):
            reasons.append("daily_lineage_unknown")
        if reject_estimated_daily_amount and bool(daily_estimated.loc[i]):
            reasons.append("daily_amount_estimated")
        if reject_unverified_bse_stitching and bool(daily_stitched.loc[i]):
            reasons.append("bse_stitching_unverified")

        label = pd.to_numeric(pd.Series([row.get("forward_opportunity_pct")]), errors="coerce").iloc[0]
        if pd.isna(label):
            reasons.append("label_missing")
        code = str(row.get("code", "")).zfill(6)
        if code not in minute_quality:
            reasons.append("minute_coverage_missing")
        else:
            reasons.extend(minute_quality[code])

        # Preserve deterministic order while preventing duplicate reason strings.
        reasons = list(dict.fromkeys(reasons))
        for reason in reasons:
            counter[reason] += 1
        all_reasons.append("|".join(reasons))
        eligible.append(not reasons)

    out["oos_eligible"] = eligible
    out["oos_exclusion_reasons"] = all_reasons
    labeled_mask = pd.to_numeric(out.get("forward_opportunity_pct"), errors="coerce").notna()
    eligible_mask = out["oos_eligible"].astype(bool)
    summary = QualityGateSummary(
        rows=int(len(out)),
        labeled_rows=int(labeled_mask.sum()),
        eligible_rows=int(eligible_mask.sum()),
        excluded_rows=int((~eligible_mask).sum()),
        eligible_codes=int(out.loc[eligible_mask, "code"].nunique()),
        exclusion_reasons=dict(sorted(counter.items())),
        daily_adjust=str(required_daily_adjust),
        minute_adjust=str(required_minute_adjust),
    )
    return out, summary
