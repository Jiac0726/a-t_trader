from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

import pandas as pd

from calibration.cross_sectional import combine_labeled_panels
from calibration.walkforward import attach_forward_labels, build_opportunity_history, build_score_history
from providers.security_master import filter_panel_by_lifecycle, filter_panel_by_snapshots, point_in_time_from_lifecycle


@dataclass(slots=True)
class DatasetFailure:
    code: str
    stage: str
    error: str


@dataclass(slots=True)
class ResearchDatasetBuild:
    requested_codes: int
    daily_ready_codes: int
    minute_ready_codes: int
    panel_rows: int
    labeled_rows: int
    failures: list[DatasetFailure] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _codes(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(x).strip().zfill(6) for x in values if str(x).strip()))


def universe_codes_from_lifecycle(master: pd.DataFrame, start, end) -> list[str]:
    """Return the union of securities alive at either boundary of a research range.

    This is a download-planning helper, not the final point-in-time membership
    filter. Exact score rows should still be filtered by snapshots where they are
    available. Including both boundaries prevents currently delisted-but-relevant
    historical names from disappearing from the research download plan.
    """
    if master is None or master.empty:
        return []
    start_frame = point_in_time_from_lifecycle(master, start)
    end_frame = point_in_time_from_lifecycle(master, end)
    # Also include securities whose lifecycle intersects the interior even when
    # they are absent at both boundaries (IPO and delist inside the range).
    x = master.copy()
    x["ipo_date"] = pd.to_datetime(x.get("ipo_date"), errors="coerce").dt.normalize()
    x["delist_date"] = pd.to_datetime(x.get("delist_date"), errors="coerce").dt.normalize()
    start_ts, end_ts = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    intersects = x["ipo_date"].notna() & (x["ipo_date"] <= end_ts)
    intersects &= x["delist_date"].isna() | (x["delist_date"] >= start_ts)
    interior = x.loc[intersects]
    parts = [frame.get("code", pd.Series(dtype=str)) for frame in (start_frame, end_frame, interior)]
    return _codes(pd.concat(parts, ignore_index=True).tolist()) if parts else []


def build_daily_score_panel(
    codes: Iterable[str],
    store,
    *,
    start=None,
    end=None,
    lookback: int = 60,
    master: pd.DataFrame | None = None,
    snapshots: pd.DataFrame | None = None,
    include_suspended: bool = False,
) -> tuple[pd.DataFrame, list[DatasetFailure]]:
    """Build date×stock score rows strictly from daily bars already in storage.

    Network hydration is deliberately outside this function. That separation
    makes dataset construction deterministic/restartable: downloading can fail
    independently, while already-cached bars can always be rebuilt into the same
    score panel.
    """
    failures: list[DatasetFailure] = []
    frames: list[pd.DataFrame] = []
    for code in _codes(codes):
        try:
            daily = store.load_history(code, "1d", start=start, end=end)
            if daily is None or daily.empty or len(daily) < int(lookback):
                failures.append(DatasetFailure(code, "daily_score", f"insufficient daily rows: {0 if daily is None else len(daily)}"))
                continue
            name = ""
            if master is not None and not master.empty and "code" in master.columns:
                m = master.copy()
                m["code"] = m["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
                hit = m[m["code"] == code]
                if not hit.empty and "name" in hit.columns:
                    name = str(hit.iloc[-1]["name"] or "")
            frame = build_score_history(code, daily, lookback=lookback, name=name, provider="duckdb")
            if not frame.empty:
                frame["code"] = code
                frames.append(frame)
        except Exception as exc:
            failures.append(DatasetFailure(code, "daily_score", str(exc)))
    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if panel.empty:
        return panel, failures
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.normalize()
    panel = panel.sort_values(["date", "code"]).reset_index(drop=True)
    if master is not None:
        panel = filter_panel_by_lifecycle(panel, master, unknown="drop")
    if snapshots is not None:
        panel = filter_panel_by_snapshots(
            panel,
            snapshots,
            include_suspended=include_suspended,
            unknown_dates="keep",
        )
    return panel, failures


def attach_minute_labels_from_store(
    score_panel: pd.DataFrame,
    store,
    *,
    codes: Iterable[str] | None = None,
    horizon: int = 5,
    mode: str = "positive",
    bottom_shares: int = 1000,
    t_ratio: float = 0.5,
) -> tuple[pd.DataFrame, list[DatasetFailure]]:
    """Attach future T-opportunity labels wherever 5-minute cache really exists.

    Missing minute history stays missing. We never manufacture a 500-day label
    window merely because the daily panel is long.
    """
    if score_panel is None or score_panel.empty:
        return pd.DataFrame(), []
    wanted = _codes(codes if codes is not None else score_panel["code"].unique())
    failures: list[DatasetFailure] = []
    labeled_by_code: dict[str, pd.DataFrame] = {}
    for code in wanted:
        scores = score_panel[score_panel["code"].astype(str).str.zfill(6) == code].copy()
        if scores.empty:
            continue
        try:
            minute = store.load_history(code, "5m")
            if minute is None or minute.empty:
                failures.append(DatasetFailure(code, "minute_label", "no cached 5m rows"))
                labeled_by_code[code] = scores.assign(forward_opportunity_pct=float("nan"), forward_days=0)
                continue
            opportunities = build_opportunity_history(
                minute,
                mode=mode,
                bottom_shares=bottom_shares,
                t_ratio=t_ratio,
            )
            labeled_by_code[code] = attach_forward_labels(scores, opportunities, horizon=horizon)
        except Exception as exc:
            failures.append(DatasetFailure(code, "minute_label", str(exc)))
            labeled_by_code[code] = scores.assign(forward_opportunity_pct=float("nan"), forward_days=0)
    labeled = combine_labeled_panels(labeled_by_code)
    return labeled, failures


def select_minute_candidates(score_panel: pd.DataFrame, limit: int) -> list[str]:
    """Choose a bounded minute-hydration batch from the latest daily score rows.

    Score is primary, recent median amount is the tie-breaker. This keeps the
    expensive minute stage aligned with the product goal instead of arbitrary
    lexical code order.
    """
    if score_panel is None or score_panel.empty or int(limit) <= 0:
        return []
    x = score_panel.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x["code"] = x["code"].astype(str).str.zfill(6)
    latest = x.sort_values(["code", "date"]).groupby("code", as_index=False).tail(1)
    latest["score"] = pd.to_numeric(latest.get("score"), errors="coerce").fillna(-1)
    latest["median_amount"] = pd.to_numeric(latest.get("median_amount"), errors="coerce").fillna(0)
    latest = latest.sort_values(["score", "median_amount", "code"], ascending=[False, False, True])
    return latest["code"].head(int(limit)).tolist()


def minute_coverage_report(codes: Iterable[str], store) -> pd.DataFrame:
    """Describe actual cached 5-minute availability; no provider assumptions."""
    rows: list[dict] = []
    for code in _codes(codes):
        low, high = store.history_bounds(code, "5m")
        frame = store.load_history(code, "5m") if low is not None else pd.DataFrame()
        trading_days = 0
        if frame is not None and not frame.empty and "datetime" in frame.columns:
            trading_days = int(pd.to_datetime(frame["datetime"], errors="coerce").dt.normalize().nunique())
        rows.append(
            {
                "code": code,
                "first_5m": low,
                "last_5m": high,
                "rows_5m": 0 if frame is None else int(len(frame)),
                "trading_days_5m": trading_days,
            }
        )
    return pd.DataFrame(rows).sort_values("code").reset_index(drop=True)
