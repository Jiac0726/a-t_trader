from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import pandas as pd
import requests

from .base import MarketDataError


class _HtmlTableParser(HTMLParser):
    """Tiny stdlib table parser so the core path does not require bs4/lxml."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            value = "".join(self._cell).replace("\xa0", " ").strip()
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


class BseCodeMappingProvider:
    """Official BSE legacy-code -> 920-code mapping.

    BSE switched legacy listed-company codes to the 920 namespace on
    2025-10-09. The mapping is not algorithmic: collision handling means some
    new codes do not share the old code's final three digits. This provider
    therefore reads the official mapping table and never guesses a mapping.
    """

    name = "bse-official-code-mapping"
    url = "https://www.bse.cn/service/code_mapping.html"
    switch_date = pd.Timestamp("2025-10-09")

    def __init__(self, timeout: float = 15.0, session: requests.Session | None = None):
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
                "Referer": "https://www.bse.cn/",
            }
        )
        self._cache: pd.DataFrame | None = None

    @staticmethod
    def _code(value: Any) -> str:
        text = str(value or "").strip()
        return text.zfill(6) if text.isdigit() else ""

    @classmethod
    def parse_html(cls, html: str) -> pd.DataFrame:
        parser = _HtmlTableParser()
        parser.feed(html or "")

        header_index = None
        header: list[str] = []
        for i, row in enumerate(parser.rows):
            normalized = [str(x).replace(" ", "") for x in row]
            if "旧代码" in normalized and "新代码" in normalized:
                header_index = i
                header = normalized
                break
        if header_index is None:
            raise MarketDataError("BSE mapping page does not contain old/new code table headers")

        old_idx = header.index("旧代码")
        new_idx = header.index("新代码")
        name_idx = header.index("证券简称") if "证券简称" in header else None
        listing_idx = header.index("上市日期") if "上市日期" in header else None

        records: list[dict[str, Any]] = []
        for row in parser.rows[header_index + 1 :]:
            if max(old_idx, new_idx) >= len(row):
                continue
            old_code = cls._code(row[old_idx])
            new_code = cls._code(row[new_idx])
            if not old_code or not new_code:
                continue
            records.append(
                {
                    "old_code": old_code,
                    "new_code": new_code,
                    "name": str(row[name_idx]).strip() if name_idx is not None and name_idx < len(row) else "",
                    "listing_date": pd.to_datetime(row[listing_idx], errors="coerce") if listing_idx is not None and listing_idx < len(row) else pd.NaT,
                    "switch_date": cls.switch_date,
                    "source": "bse-official-code-mapping",
                }
            )

        out = pd.DataFrame(records)
        if out.empty:
            raise MarketDataError("BSE mapping page produced no code pairs")
        if out["old_code"].duplicated().any():
            raise MarketDataError("BSE mapping contains duplicate old codes")
        if out["new_code"].duplicated().any():
            raise MarketDataError("BSE mapping contains duplicate new codes")
        if not out["new_code"].str.startswith("920").all():
            raise MarketDataError("BSE mapping contains unexpected non-920 new code")
        return out.sort_values(["listing_date", "new_code"], ascending=[False, True], na_position="last").reset_index(drop=True)

    def mapping(self, *, refresh: bool = False) -> pd.DataFrame:
        if self._cache is not None and not refresh:
            return self._cache.copy()
        try:
            response = self.session.get(self.url, timeout=self.timeout)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            out = self.parse_html(response.text)
        except Exception as exc:
            if isinstance(exc, MarketDataError):
                raise
            raise MarketDataError(f"BSE code mapping request failed: {exc}") from exc
        if len(out) < 100:
            raise MarketDataError(f"BSE code mapping suspiciously small: {len(out)}")
        self._cache = out.copy()
        return out

    def old_code_for(self, new_code: str) -> str | None:
        code = self._code(new_code)
        rows = self.mapping()
        hit = rows.loc[rows["new_code"] == code, "old_code"]
        return None if hit.empty else str(hit.iloc[0])

    def new_code_for(self, old_code: str) -> str | None:
        code = self._code(old_code)
        rows = self.mapping()
        hit = rows.loc[rows["old_code"] == code, "new_code"]
        return None if hit.empty else str(hit.iloc[0])
