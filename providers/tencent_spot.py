from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

import pandas as pd
import requests

from .base import MarketDataError

_LINE_RE = re.compile(r'^v_([^=]+)="(.*)";?$')


@dataclass(frozen=True)
class TencentSpotReport:
    requested: int
    returned: int
    batches: int
    failed_batches: int


class TencentSpotProvider:
    """Batch current quotes from Tencent Finance.

    This source is intentionally separated from security identity and history.
    It is used only for cheap first-stage market screening metrics.
    """

    name = "tencent-spot"
    url = "https://qt.gtimg.cn/q="

    def __init__(self, timeout: float = 10.0, batch_size: int = 80, session: requests.Session | None = None):
        self.timeout = float(timeout)
        self.batch_size = max(1, min(150, int(batch_size)))
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 a-t-trader/0.2"})
        self.last_report = TencentSpotReport(0, 0, 0, 0)

    @staticmethod
    def _prefix(code: str) -> str:
        code = str(code).strip().zfill(6)
        if code.startswith(("92", "8", "4")):
            return "bj"
        if code.startswith(("5", "6")):
            return "sh"
        return "sz"

    @staticmethod
    def _market(prefix: str) -> str:
        return {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(prefix, "")

    @staticmethod
    def _num(fields: list[str], idx: int) -> float:
        try:
            value = fields[idx]
            return float(value) if value not in (None, "", "-") else float("nan")
        except (IndexError, TypeError, ValueError):
            return float("nan")

    @classmethod
    def _parse_line(cls, line: str) -> dict | None:
        m = _LINE_RE.match(line.strip())
        if not m:
            return None
        symbol, payload = m.groups()
        fields = payload.split("~")
        if len(fields) < 53:
            return None
        prefix = symbol[:2]
        code = str(fields[2] or symbol[2:]).strip().zfill(6)
        price = cls._num(fields, 3)
        prev_close = cls._num(fields, 4)
        high = cls._num(fields, 33)
        low = cls._num(fields, 34)
        amount_wan = cls._num(fields, 37)
        amplitude = cls._num(fields, 43)
        if pd.isna(amplitude) and pd.notna(prev_close) and prev_close > 0 and pd.notna(high) and pd.notna(low):
            amplitude = (high - low) / prev_close * 100.0
        return {
            "code": code,
            "name": str(fields[1] or ""),
            "market": cls._market(prefix),
            "price": price,
            "prev_close": prev_close,
            "open": cls._num(fields, 5),
            "pct_change": cls._num(fields, 32),
            "high": high,
            "low": low,
            "amplitude": amplitude,
            "volume_lots": cls._num(fields, 36),
            "amount": amount_wan * 10000.0 if pd.notna(amount_wan) else float("nan"),
            "turnover": cls._num(fields, 38),
            "total_mcap_yi": cls._num(fields, 44),
            "float_mcap_yi": cls._num(fields, 45),
            "quote_time": str(fields[30] or ""),
            "source": cls.name,
        }

    def quotes(self, codes: Iterable[str]) -> pd.DataFrame:
        normalized = list(dict.fromkeys(str(c).strip().zfill(6) for c in codes if str(c).strip()))
        rows: list[dict] = []
        failed = 0
        batches = 0
        for start in range(0, len(normalized), self.batch_size):
            batch = normalized[start : start + self.batch_size]
            symbols = [self._prefix(code) + code for code in batch]
            batches += 1
            try:
                response = self.session.get(self.url + ",".join(symbols), timeout=self.timeout)
                response.raise_for_status()
                response.encoding = "gbk"
                for line in response.text.splitlines():
                    parsed = self._parse_line(line)
                    if parsed is not None:
                        rows.append(parsed)
            except Exception:
                failed += 1
                continue
        out = pd.DataFrame(rows)
        if not out.empty:
            out = out.drop_duplicates(["market", "code"], keep="last").reset_index(drop=True)
        self.last_report = TencentSpotReport(len(normalized), len(out), batches, failed)
        if len(normalized) and out.empty:
            raise MarketDataError("Tencent spot quote batches returned no parseable rows")
        if not out.empty:
            out.attrs["provider"] = self.name
            out.attrs["report"] = self.last_report
        return out
