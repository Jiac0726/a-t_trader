from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd
import requests

from .base import MarketDataError, MarketDataProvider

_TAG_RE = re.compile(r"<[^>]+>")


class OfficialExchangeUniverseProvider(MarketDataProvider):
    """Current A-share identity universe from SSE, SZSE and BSE official endpoints.

    This provider intentionally supplies *identity only*: code/name/market/listing
    date. It does not manufacture spot amount, price or K-lines. Price providers
    remain independently replaceable, and callers that require real-time fields
    must fail explicitly when those fields are absent.
    """

    name = "official-exchange-universe"
    _SSE_URL = "https://query.sse.com.cn/sseQuery/commonQuery.do"
    _SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
    _BSE_URL = "https://www.bse.cn/nqxxController/nqxxCnzq.do"

    def __init__(self, timeout: float = 15.0, session: requests.Session | None = None):
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
            }
        )

    @staticmethod
    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        return _TAG_RE.sub("", str(value)).strip()

    @staticmethod
    def _code(value: Any) -> str:
        text = str(value or "").strip().split(".")[0]
        return text.zfill(6) if text.isdigit() else ""

    def _sse_board(self, stock_type: str) -> list[dict[str, Any]]:
        params = {
            "STOCK_TYPE": stock_type,
            "REG_PROVINCE": "",
            "CSRC_CODE": "",
            "STOCK_CODE": "",
            "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
            "COMPANY_STATUS": "2,4,5,7,8",
            "type": "inParams",
            "isPagination": "true",
            "pageHelp.cacheSize": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.pageSize": "10000",
            "pageHelp.pageNo": "1",
            "pageHelp.endPage": "1",
        }
        headers = {
            "Referer": "https://www.sse.com.cn/assortment/stock/list/share/",
            "User-Agent": self.session.headers.get("User-Agent", "Mozilla/5.0"),
        }
        r = self.session.get(self._SSE_URL, params=params, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("result") or []
        return rows if isinstance(rows, list) else []

    def _sse(self) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for stock_type in ("1", "8"):
            for row in self._sse_board(stock_type):
                code = self._code(row.get("A_STOCK_CODE"))
                if not code:
                    continue
                records.append(
                    {
                        "code": code,
                        "name": self._clean_text(row.get("SEC_NAME_CN")),
                        "market": "SH",
                        "listing_date": pd.to_datetime(row.get("LIST_DATE"), errors="coerce"),
                        "source": "sse-official",
                    }
                )
        out = pd.DataFrame(records)
        if len(out) < 1000:
            raise MarketDataError(f"SSE official universe suspiciously small: {len(out)}")
        return out

    @staticmethod
    def _szse_sections(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict) and isinstance(item.get("data"), list)]

    def _szse(self) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        page = 1
        max_pages = 500
        while page <= max_pages:
            params = {
                "SHOWTYPE": "JSON",
                "CATALOGID": "1110",
                "TABKEY": "tab1",
                "PAGENO": str(page),
                "random": "0.9",
            }
            headers = {
                "Referer": "https://www.szse.cn/market/product/stock/list/",
                "User-Agent": self.session.headers.get("User-Agent", "Mozilla/5.0"),
            }
            r = self.session.get(self._SZSE_URL, params=params, headers=headers, timeout=self.timeout)
            r.raise_for_status()
            sections = self._szse_sections(r.json())
            page_rows: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            for section in sections:
                if not metadata and isinstance(section.get("metadata"), dict):
                    metadata = section["metadata"]
                page_rows.extend(item for item in section.get("data", []) if isinstance(item, dict))
            for row in page_rows:
                code = self._code(row.get("agdm") or row.get("zqdm") or row.get("code") or row.get("dm"))
                if not code:
                    continue
                records.append(
                    {
                        "code": code,
                        "name": self._clean_text(row.get("agjc") or row.get("zqjc") or row.get("name") or code),
                        "market": "SZ",
                        "listing_date": pd.to_datetime(row.get("agssrq") or row.get("ssrq"), errors="coerce"),
                        "source": "szse-official",
                    }
                )
            page_count = metadata.get("pagecount")
            record_count = metadata.get("recordcount")
            if not page_rows:
                break
            try:
                if page_count is not None and page >= int(page_count):
                    break
            except (TypeError, ValueError):
                pass
            try:
                if record_count is not None and len(records) >= int(record_count):
                    break
            except (TypeError, ValueError):
                pass
            page += 1
        out = pd.DataFrame(records).drop_duplicates("code", keep="last") if records else pd.DataFrame()
        if len(out) < 1000:
            raise MarketDataError(f"SZSE official universe suspiciously small: {len(out)}")
        return out

    @staticmethod
    def _parse_bse_text(text: str) -> list[dict[str, Any]]:
        left, right = text.find("["), text.rfind("]")
        if left < 0 or right <= left:
            raise MarketDataError("BSE official response does not contain JSON array")
        value = json.loads(text[left : right + 1])
        return value if isinstance(value, list) else []

    @classmethod
    def _bse_row(cls, row: Any) -> dict[str, Any] | None:
        if isinstance(row, dict):
            code = cls._code(row.get("xxzqdm") or row.get("证券代码") or row.get("code"))
            name = cls._clean_text(row.get("xxzqjc") or row.get("证券简称") or row.get("name"))
            listing = row.get("xxssrq") or row.get("上市日期")
        elif isinstance(row, (list, tuple)) and len(row) > 40:
            # Matches the current BSE listed-company response schema used by
            # AKShare: listing date at index 0, code at 38, short name at 40.
            listing, code, name = row[0], cls._code(row[38]), cls._clean_text(row[40])
        else:
            return None
        if not code:
            return None
        return {
            "code": code,
            "name": name or code,
            "market": "BJ",
            "listing_date": pd.to_datetime(listing, errors="coerce"),
            "source": "bse-official",
        }

    def _bse(self) -> pd.DataFrame:
        payload = {
            "page": "0",
            "typejb": "T",
            "xxfcbj[]": "2",
            "xxzqdm": "",
            "sortfield": "xxzqdm",
            "sorttype": "asc",
        }
        headers = {
            "Origin": "https://www.bse.cn",
            "Referer": "https://www.bse.cn/nq/listedcompany.html",
            "User-Agent": self.session.headers.get("User-Agent", "Mozilla/5.0"),
        }
        records: list[dict[str, Any]] = []
        total_pages: int | None = None
        page = 0
        while total_pages is None or page < total_pages:
            payload["page"] = str(page)
            r = self.session.post(self._BSE_URL, data=payload, headers=headers, timeout=self.timeout)
            r.raise_for_status()
            data = self._parse_bse_text(r.text)
            if not data:
                break
            envelope = data[0] if isinstance(data[0], dict) else {}
            try:
                total_pages = int(envelope.get("totalPages"))
            except (TypeError, ValueError):
                total_pages = 1
            content = envelope.get("content") or []
            for raw in content:
                parsed = self._bse_row(raw)
                if parsed:
                    records.append(parsed)
            page += 1
            if page >= 100:
                break
        out = pd.DataFrame(records).drop_duplicates("code", keep="last") if records else pd.DataFrame()
        if len(out) < 100:
            raise MarketDataError(f"BSE official universe suspiciously small: {len(out)}")
        return out

    def stock_list(self) -> pd.DataFrame:
        try:
            parts = [self._sse(), self._szse(), self._bse()]
        except Exception as exc:
            if isinstance(exc, MarketDataError):
                raise
            raise MarketDataError(f"Official exchange universe failed: {exc}") from exc
        out = pd.concat(parts, ignore_index=True)
        out["code"] = out["code"].astype(str).str.zfill(6)
        out = out.drop_duplicates(["market", "code"], keep="last").sort_values(["market", "code"]).reset_index(drop=True)
        markets = set(out["market"])
        if markets != {"SH", "SZ", "BJ"}:
            raise MarketDataError(f"Official exchange universe missing markets: {sorted({'SH','SZ','BJ'} - markets)}")
        if len(out) < 3000:
            raise MarketDataError(f"Official exchange universe suspiciously small: {len(out)}")
        out.attrs["provider"] = self.name
        return out

    def history(self, code, start, end, interval="1d", adjust="qfq") -> pd.DataFrame:
        raise MarketDataError("Official exchange universe provider supplies identity only, not price history")
