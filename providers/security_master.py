from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

SNAPSHOT_COLUMNS = [
    "as_of",
    "code",
    "exchange",
    "name",
    "trade_status",
    "source_code",
    "source",
]

MASTER_COLUMNS = [
    "code",
    "exchange",
    "name",
    "ipo_date",
    "delist_date",
    "security_type",
    "listed_status",
    "source_code",
    "source",
]


class SecurityMasterProvider(ABC):
    """Independent point-in-time security-universe source."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self, as_of: date | str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def master(self) -> pd.DataFrame:
        raise NotImplementedError

    def snapshot_many(self, as_of_dates) -> pd.DataFrame:
        parts: list[pd.DataFrame] = []
        for value in sorted({pd.Timestamp(x).normalize() for x in as_of_dates}):
            frame = self.snapshot(value)
            if frame is not None and not frame.empty:
                parts.append(frame)
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=SNAPSHOT_COLUMNS)


def normalize_exchange_code(source_code: str) -> tuple[str, str]:
    raw = str(source_code or "").strip().lower()
    if "." not in raw:
        return "", raw.zfill(6) if raw.isdigit() else raw
    exchange, code = raw.split(".", 1)
    exchange = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(exchange, exchange.upper())
    return exchange, code.zfill(6) if code.isdigit() else code


def looks_like_a_share_stock(exchange: str, code: str) -> bool:
    """Conservative stock filter for historical all-security snapshots."""
    exchange = str(exchange).upper()
    code = str(code).zfill(6)
    if exchange == "SH":
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    if exchange == "SZ":
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if exchange == "BJ":
        return code.startswith(("43", "83", "87", "88", "92"))
    return False


def point_in_time_from_lifecycle(master: pd.DataFrame, as_of: date | str) -> pd.DataFrame:
    """IPO/delist-date fallback when exact historical snapshots are unavailable."""
    if master is None or master.empty:
        return pd.DataFrame(columns=MASTER_COLUMNS)
    as_of_ts = pd.Timestamp(as_of).normalize()
    x = master.copy()
    x["ipo_date"] = pd.to_datetime(x.get("ipo_date"), errors="coerce")
    x["delist_date"] = pd.to_datetime(x.get("delist_date"), errors="coerce")
    if "security_type" in x.columns:
        x = x[x["security_type"].astype(str).str.lower().isin({"stock", "1"})]
    mask = x["ipo_date"].notna() & (x["ipo_date"] <= as_of_ts)
    mask &= x["delist_date"].isna() | (x["delist_date"] >= as_of_ts)
    return x.loc[mask].sort_values(["exchange", "code"]).reset_index(drop=True)


def filter_panel_by_lifecycle(panel: pd.DataFrame, master: pd.DataFrame, *, unknown: str = "drop") -> pd.DataFrame:
    """Remove future-IPO/post-delist rows from a date × stock panel."""
    if unknown not in {"drop", "keep"}:
        raise ValueError("unknown must be 'drop' or 'keep'")
    if panel is None or panel.empty:
        return panel.copy() if isinstance(panel, pd.DataFrame) else pd.DataFrame()
    if {"date", "code"} - set(panel.columns):
        raise ValueError("panel must contain date and code")
    if master is None or master.empty:
        return panel.copy() if unknown == "keep" else panel.iloc[0:0].copy()
    missing = {"code", "ipo_date", "delist_date"} - set(master.columns)
    if missing:
        raise ValueError(f"master missing columns: {sorted(missing)}")

    x = panel.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    x["code"] = x["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    m = master.copy()
    m["code"] = m["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    if "security_type" in m.columns:
        m = m[m["security_type"].astype(str).str.lower().isin({"stock", "1"})]
    m["ipo_date"] = pd.to_datetime(m["ipo_date"], errors="coerce").dt.normalize()
    m["delist_date"] = pd.to_datetime(m["delist_date"], errors="coerce").dt.normalize()
    m = m.sort_values(["code", "ipo_date"]).drop_duplicates("code", keep="last")
    life = m[["code", "ipo_date", "delist_date"]].rename(columns={"ipo_date": "__ipo_date", "delist_date": "__delist_date"})
    out = x.merge(life, on="code", how="left", validate="many_to_one")
    known = out["__ipo_date"].notna()
    active = known & (out["date"] >= out["__ipo_date"])
    active &= out["__delist_date"].isna() | (out["date"] <= out["__delist_date"])
    if unknown == "keep":
        active |= ~known
    out = out.loc[active].drop(columns=["__ipo_date", "__delist_date"])
    return out.sort_values(["date", "code"]).reset_index(drop=True)


def filter_panel_by_snapshots(panel: pd.DataFrame, snapshots: pd.DataFrame, *, include_suspended: bool = True, unknown_dates: str = "drop") -> pd.DataFrame:
    """Exact date × code membership filter using historical snapshots."""
    if unknown_dates not in {"drop", "keep"}:
        raise ValueError("unknown_dates must be 'drop' or 'keep'")
    if panel is None or panel.empty:
        return panel.copy() if isinstance(panel, pd.DataFrame) else pd.DataFrame()
    x = panel.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    x["code"] = x["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    if snapshots is None or snapshots.empty:
        return x if unknown_dates == "keep" else x.iloc[0:0].copy()
    s = snapshots.copy()
    s["as_of"] = pd.to_datetime(s["as_of"], errors="coerce").dt.normalize()
    s["code"] = s["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    if not include_suspended and "trade_status" in s.columns:
        s = s[pd.to_numeric(s["trade_status"], errors="coerce").fillna(0).astype(int) == 1]
    membership = s[["as_of", "code"]].dropna().drop_duplicates().assign(__member=True)
    out = x.merge(membership, left_on=["date", "code"], right_on=["as_of", "code"], how="left")
    known_dates = set(s["as_of"].dropna().unique())
    member = out["__member"].eq(True)
    if unknown_dates == "keep":
        member |= ~out["date"].isin(known_dates)
    return out.loc[member, x.columns].sort_values(["date", "code"]).reset_index(drop=True)
