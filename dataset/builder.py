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


def _load_history(store, code: str, interval: str, *, start=None, end=None, adjust: str):
    try:
        return store.load_history(code, interval, start=start, end=end, adjust=adjust)
    except TypeError:
        # Compatibility for tiny in-memory test stores and old notebooks.
        return store.load_history(code, interval, start=start, end=end)


def _history_bounds(store, code: str, interval: str, *, adjust: str):
    try:
        return store.history_bounds(code, interval, adjust=adjust)
    except TypeError:
        return store.history_bounds(code, interval)


def universe_codes_from_lifecycle(master: pd.DataFrame, start, end) -> list[str]:
    """Return securities whose lifecycle intersects the requested range."""
    if master is None or master.empty:
        return []
    start_frame = point_in_time_from_lifecycle(master, start)
    end_frame = point_in_time_from_lifecycle(master, end)
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
    adjust: str = "qfq",
    master: pd.DataFrame | None = None,
    snapshots: pd.DataFrame | None = None,
    include_suspended: bool = False,
) -> tuple[pd.DataFrame, list[DatasetFailure]]:
    """Build date×stock score rows from cached daily bars.

    Daily feature research defaults to qfq because continuity-based amplitude,
    trend and drawdown features should not interpret corporate-action gaps as
    tradable moves. The selected adjustment namespace is carried into row-level
    lineage by ``build_score_history``.
    """
    failures: list[DatasetFailure] = []
    frames: list[pd.DataFrame] = []
    for code in _codes(codes):
        try:
            daily = _load_history(store, code, "1d", start=start, end=end, adjust=adjust)
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
    adjust: str = "none",
) -> tuple[pd.DataFrame, list[DatasetFailure]]:
    """Attach future T-opportunity labels wherever real 5-minute cache exists.

    T labels default to **raw/unadjusted** minute prices. Fixed minimum
    commission, share counts and nominal P&L depend on the historical traded
    price level; using qfq minute prices would distort those transaction-cost
    thresholds. Missing minute history stays missing and is never manufactured.
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
            minute = _load_history(store, code, "5m", adjust=adjust)
            if minute is None or minute.empty:
                failures.append(DatasetFailure(code, "minute_label", f"no cached 5m rows for adjust={adjust}"))
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
    if not labeled.empty:
        labeled["label_adjust"] = adjust
    return labeled, failures


def select_minute_candidates(score_panel: pd.DataFrame, limit: int) -> list[str]:
    """Choose a bounded minute-hydration batch from the latest daily score rows."""
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


def minute_coverage_report(codes: Iterable[str], store, *, adjust: str = "none") -> pd.DataFrame:
    """Describe actual cached 5-minute availability for one adjustment namespace."""
    rows: list[dict] = []
    for code in _codes(codes):
        low, high = _history_bounds(store, code, "5m", adjust=adjust)
        frame = _load_history(store, code, "5m", adjust=adjust) if low is not None else pd.DataFrame()
        trading_days = 0
        providers = ""
        qualities = ""
        if frame is not None and not frame.empty:
            if "datetime" in frame.columns:
                trading_days = int(pd.to_datetime(frame["datetime"], errors="coerce").dt.normalize().nunique())
            if "provider" in frame.columns:
                providers = "|".join(sorted(frame["provider"].dropna().astype(str).unique()))
            if "amount_quality" in frame.columns:
                qualities = "|".join(sorted(frame["amount_quality"].dropna().astype(str).unique()))
        rows.append(
            {
                "code": code,
                "adjust": adjust,
                "first_5m": low,
                "last_5m": high,
                "rows_5m": 0 if frame is None else int(len(frame)),
                "trading_days_5m": trading_days,
                "providers": providers,
                "amount_quality": qualities,
            }
        )
    return pd.DataFrame(rows).sort_values("code").reset_index(drop=True)
