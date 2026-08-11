from __future__ import annotations

import os
import struct
from pathlib import Path

import pandas as pd

from .base import MarketDataError, MarketDataProvider, NoMarketData


class TdxLocalHistoryProvider(MarketDataProvider):
    """Read raw local TongdaXin client K-line files without any network access.

    Supported layouts:
      <root>/vipdoc/sh/lday/sh600519.day
      <root>/vipdoc/sz/lday/sz000001.day
      <root>/vipdoc/bj/lday/bj920002.day
      <root>/vipdoc/{sh,sz,bj}/minline/<symbol>.lc1
      <root>/vipdoc/{sh,sz,bj}/fzline/<symbol>.lc5

    Local TDX bars are raw/unadjusted. This provider therefore rejects qfq/hfq
    requests instead of silently mixing adjustment conventions into the T Score
    research panel. It is an explicit offline/backfill source until a separate
    corporate-action adjustment layer is validated.
    """

    name = "tdx-local"
    _DAY = struct.Struct("<IIIIIfII")
    _MIN = struct.Struct("<HHfffffII")

    def __init__(self, root: str | os.PathLike | None = None):
        configured = root if root is not None else os.getenv("TDX_ROOT", "")
        self.root = Path(configured).expanduser() if configured else None

    @property
    def available(self) -> bool:
        return bool(self.root and self.root.exists())

    @staticmethod
    def _market(code: str) -> str:
        code = str(code).strip().lower()
        if code.startswith(("sh", "sz", "bj")):
            return code[:2]
        code = code.zfill(6)
        if code.startswith(("920", "4", "8")):
            return "bj"
        if code.startswith(("5", "6", "9")):
            return "sh"
        return "sz"

    @classmethod
    def _symbol(cls, code: str) -> tuple[str, str]:
        raw = str(code).strip().lower()
        if raw.startswith(("sh", "sz", "bj")):
            return raw[:2], raw[2:].zfill(6)
        symbol = raw.zfill(6)
        return cls._market(symbol), symbol

    def _require_root(self) -> Path:
        if not self.available:
            raise MarketDataError("TDX local provider requires an existing TDX_ROOT")
        assert self.root is not None
        return self.root

    def _path(self, code: str, interval: str) -> Path:
        root = self._require_root()
        market, symbol = self._symbol(code)
        prefix = f"{market}{symbol}"
        if interval in {"1d", "day"}:
            return root / "vipdoc" / market / "lday" / f"{prefix}.day"
        if interval == "1m":
            return root / "vipdoc" / market / "minline" / f"{prefix}.lc1"
        if interval == "5m":
            return root / "vipdoc" / market / "fzline" / f"{prefix}.lc5"
        raise NoMarketData(f"TDX local provider does not support interval {interval}")

    @staticmethod
    def _valid_date(value: int) -> pd.Timestamp | None:
        text = str(int(value))
        if len(text) != 8:
            return None
        parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
        return None if pd.isna(parsed) else pd.Timestamp(parsed)

    @staticmethod
    def _minute_datetime(date_num: int, minute_num: int) -> pd.Timestamp | None:
        year = int(date_num) // 2048 + 2004
        remainder = int(date_num) % 2048
        month = remainder // 100
        day = remainder % 100
        hour = int(minute_num) // 60
        minute = int(minute_num) % 60
        try:
            return pd.Timestamp(year=year, month=month, day=day, hour=hour, minute=minute)
        except ValueError:
            return None

    def _read_day(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            raise NoMarketData(f"TDX daily file not found: {path}")
        raw = path.read_bytes()
        complete = len(raw) // self._DAY.size
        rows = []
        for i in range(complete):
            date_num, open_i, high_i, low_i, close_i, amount, volume, _ = self._DAY.unpack_from(raw, i * self._DAY.size)
            dt = self._valid_date(date_num)
            if dt is None:
                continue
            rows.append(
                {
                    "datetime": dt,
                    "open": open_i / 100.0,
                    "high": high_i / 100.0,
                    "low": low_i / 100.0,
                    "close": close_i / 100.0,
                    "amount": float(amount),
                    "volume": float(volume),
                }
            )
        out = pd.DataFrame(rows)
        out.attrs["trailing_bytes"] = len(raw) % self._DAY.size
        return out

    def _read_minute(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            raise NoMarketData(f"TDX minute file not found: {path}")
        raw = path.read_bytes()
        complete = len(raw) // self._MIN.size
        rows = []
        for i in range(complete):
            date_num, minute_num, open_f, high_f, low_f, close_f, amount, volume, _ = self._MIN.unpack_from(raw, i * self._MIN.size)
            dt = self._minute_datetime(date_num, minute_num)
            if dt is None:
                continue
            rows.append(
                {
                    "datetime": dt,
                    "open": float(open_f),
                    "high": float(high_f),
                    "low": float(low_f),
                    "close": float(close_f),
                    "amount": float(amount),
                    "volume": float(volume),
                }
            )
        out = pd.DataFrame(rows)
        out.attrs["trailing_bytes"] = len(raw) % self._MIN.size
        return out

    def history(self, code, start, end, interval="1d", adjust="qfq") -> pd.DataFrame:
        if adjust not in {"", "none"}:
            raise NoMarketData("TDX local files are raw; qfq/hfq requires a separate validated adjustment layer")
        path = self._path(code, interval)
        out = self._read_day(path) if interval in {"1d", "day"} else self._read_minute(path)
        if out.empty:
            raise NoMarketData(f"TDX local file contained no parseable bars: {path}")
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        if interval not in {"1d", "day"} and end_ts == end_ts.normalize():
            end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        out = out[(out["datetime"] >= start_ts) & (out["datetime"] <= end_ts)].copy().reset_index(drop=True)
        if out.empty:
            raise NoMarketData(f"TDX local file has no bars in requested range for {code}")
        market, symbol = self._symbol(code)
        out.attrs.update(
            {
                "provider": self.name,
                "code": symbol,
                "market": market.upper(),
                # Persist a platform-neutral lineage value so reports and
                # manifests remain comparable across Windows/Linux runners.
                "source_path": path.as_posix(),
                "adjust": "none",
                "amount_quality": "tdx_raw",
            }
        )
        return out

    def stock_list(self) -> pd.DataFrame:
        raise MarketDataError("TDX local history provider intentionally does not build the security universe")
