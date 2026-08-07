from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from .base import MarketDataError, NoMarketData
from .demo import DemoProvider
from .eastmoney import EastmoneyProvider
from .baostock_daily import BaostockHistoryProvider


@dataclass(frozen=True, slots=True)
class BenchmarkSpec:
    """Explicit market reference asset.

    `kind` is deliberately explicit so the same six-digit code is never guessed
    as stock/index/ETF only from its digits. For CSI indices Eastmoney exposes
    more than one market namespace in different endpoints, so candidates are
    tried in a declared order instead of hard-coding one ambiguous secid.
    """

    key: str
    code: str
    name: str
    market: str
    eastmoney_secids: tuple[str, ...]
    akshare_symbol: str
    kind: str = "index"

    @property
    def eastmoney_secid(self) -> str:
        """Backward-compatible primary secid."""
        return self.eastmoney_secids[0]


BENCHMARKS: dict[str, BenchmarkSpec] = {
    "sse": BenchmarkSpec("sse", "000001", "上证指数", "SH", ("1.000001",), "sh000001"),
    "csi300": BenchmarkSpec("csi300", "000300", "沪深300", "CSI", ("2.000300", "1.000300"), "csi000300"),
    "csi500": BenchmarkSpec("csi500", "000905", "中证500", "CSI", ("2.000905", "1.000905"), "csi000905"),
    "csi1000": BenchmarkSpec("csi1000", "000852", "中证1000", "CSI", ("2.000852", "1.000852"), "csi000852"),
    "szse": BenchmarkSpec("szse", "399001", "深证成指", "SZ", ("0.399001",), "sz399001"),
    "chinext": BenchmarkSpec("chinext", "399006", "创业板指", "SZ", ("0.399006",), "sz399006"),
    "star50": BenchmarkSpec("star50", "000688", "科创50", "SH", ("1.000688",), "sh000688"),
}

ETFS: dict[str, BenchmarkSpec] = {
    "sse50_etf": BenchmarkSpec("sse50_etf", "510050", "上证50ETF", "SH", ("1.510050",), "510050", "etf"),
    "csi300_etf_sh": BenchmarkSpec("csi300_etf_sh", "510300", "沪深300ETF(沪)", "SH", ("1.510300",), "510300", "etf"),
    "csi300_etf_sz": BenchmarkSpec("csi300_etf_sz", "159919", "沪深300ETF(深)", "SZ", ("0.159919",), "159919", "etf"),
    "csi500_etf": BenchmarkSpec("csi500_etf", "510500", "中证500ETF", "SH", ("1.510500",), "510500", "etf"),
    "chinext_etf": BenchmarkSpec("chinext_etf", "159915", "创业板ETF", "SZ", ("0.159915",), "159915", "etf"),
    "star50_etf": BenchmarkSpec("star50_etf", "588000", "科创50ETF", "SH", ("1.588000",), "588000", "etf"),
}

REFERENCE_ASSETS: dict[str, BenchmarkSpec] = {**BENCHMARKS, **ETFS}


BAOSTOCK_REFERENCE_CODES: dict[str, str] = {
    "sse": "sh.000001",
    "csi300": "sh.000300",
    "csi500": "sh.000905",
    "csi1000": "sh.000852",
    "szse": "sz.399001",
    "chinext": "sz.399006",
    "star50": "sh.000688",
    "sse50_etf": "sh.510050",
    "csi300_etf_sh": "sh.510300",
    "csi300_etf_sz": "sz.159919",
    "csi500_etf": "sh.510500",
    "chinext_etf": "sz.159915",
    "star50_etf": "sh.588000",
}


def resolve_reference_asset(value: str) -> BenchmarkSpec:
    raw = str(value).strip().lower()
    if raw in REFERENCE_ASSETS:
        return REFERENCE_ASSETS[raw]
    matches = [spec for spec in REFERENCE_ASSETS.values() if spec.code == raw]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous reference code {value}; use an explicit key: {', '.join(x.key for x in matches)}")
    raise ValueError(f"Unknown reference asset: {value}. Use one of: {', '.join(REFERENCE_ASSETS)}")


def resolve_benchmark(value: str) -> BenchmarkSpec:
    spec = resolve_reference_asset(value)
    if spec.kind != "index":
        raise ValueError(f"{value} is {spec.kind}, not an index benchmark")
    return spec


def _normalize_history(raw: pd.DataFrame, spec: BenchmarkSpec, provider: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise NoMarketData(f"{provider} returned no {spec.kind} data for {spec.key}")
    rename = {
        "date": "datetime",
        "日期": "datetime",
        "时间": "datetime",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "pct_change",
        "涨跌额": "change",
        "换手率": "turnover",
    }
    df = raw.rename(columns=rename).copy()
    required = ["datetime", "open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise MarketDataError(f"{provider} {spec.kind} schema changed; missing {missing}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    if "amount" not in df.columns:
        df["amount"] = 0.0
    for c in ["open", "close", "high", "low", "volume", "amount", "amplitude", "pct_change", "change", "turnover"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=required).sort_values("datetime").reset_index(drop=True)
    if df.empty:
        raise NoMarketData(f"{provider} returned no parseable {spec.kind} rows for {spec.key}")
    df.attrs.update(
        {
            "provider": provider,
            "benchmark": spec.key,
            "reference_asset": spec.key,
            "name": spec.name,
            "code": spec.code,
            "kind": spec.kind,
            "market": spec.market,
        }
    )
    return df


class EastmoneyBenchmarkProvider:
    name = "eastmoney-reference"

    def __init__(self, timeout: float = 12.0, min_interval: float = 0.35):
        self.client = EastmoneyProvider(timeout=timeout, min_interval=min_interval)

    def history(
        self,
        benchmark: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        spec = resolve_reference_asset(benchmark)
        errors: list[str] = []
        for secid in spec.eastmoney_secids:
            params: dict[str, Any] = {
                "secid": secid,
                "klt": self.client._klt(interval),
                "fqt": "0" if spec.kind == "index" else self.client._fqt(adjust),
                "beg": self.client._date_str(start),
                "end": self.client._date_str(end),
                "lmt": "1000000",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            }
            try:
                payload = self.client._get_json(self.client._HISTORY_URL, params)
            except Exception as exc:
                errors.append(f"{secid}: {exc}")
                continue
            data = payload.get("data") or {}
            klines = data.get("klines") or []
            if not klines:
                errors.append(f"{secid}: empty")
                continue
            rows = []
            for line in klines:
                parts = line.split(",")
                if len(parts) < 7:
                    continue
                rows.append(
                    {
                        "datetime": parts[0],
                        "open": parts[1],
                        "close": parts[2],
                        "high": parts[3],
                        "low": parts[4],
                        "volume": parts[5],
                        "amount": parts[6],
                        "amplitude": parts[7] if len(parts) > 7 else None,
                        "pct_change": parts[8] if len(parts) > 8 else None,
                        "change": parts[9] if len(parts) > 9 else None,
                        "turnover": parts[10] if len(parts) > 10 else None,
                    }
                )
            try:
                out = _normalize_history(pd.DataFrame(rows), spec, self.name)
            except Exception as exc:
                errors.append(f"{secid}: {exc}")
                continue
            out.attrs["eastmoney_secid"] = secid
            return out
        raise NoMarketData(f"Eastmoney returned no data for {spec.key}; attempts: {' | '.join(errors)}")


class AkshareBenchmarkProvider:
    name = "akshare-reference"

    @staticmethod
    def _ak():
        try:
            import akshare as ak
        except ImportError as exc:
            raise MarketDataError("AKShare is not installed. Run: pip install akshare") from exc
        return ak

    @staticmethod
    def _date_str(value: date | str) -> str:
        return value.strftime("%Y%m%d") if isinstance(value, date) else str(value).replace("-", "")[:8]

    @staticmethod
    def _datetime_str(value: date | str, end: bool = False) -> str:
        ts = pd.Timestamp(value)
        return f"{ts:%Y-%m-%d} {'15:00:00' if end else '09:30:00'}"

    def history(
        self,
        benchmark: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        spec = resolve_reference_asset(benchmark)
        ak = self._ak()
        try:
            if spec.kind == "index":
                if interval == "1d":
                    raw = ak.stock_zh_index_daily_em(
                        symbol=spec.akshare_symbol,
                        start_date=self._date_str(start),
                        end_date=self._date_str(end),
                    )
                else:
                    period = str(interval).replace("m", "")
                    raw = ak.index_zh_a_hist_min_em(
                        symbol=spec.code,
                        period=period,
                        start_date=self._datetime_str(start),
                        end_date=self._datetime_str(end, end=True),
                    )
            else:
                if interval == "1d":
                    raw = ak.fund_etf_hist_em(
                        symbol=spec.code,
                        period="daily",
                        start_date=self._date_str(start),
                        end_date=self._date_str(end),
                        adjust=adjust if adjust in {"", "qfq", "hfq"} else "qfq",
                    )
                else:
                    period = str(interval).replace("m", "")
                    raw = ak.fund_etf_hist_min_em(
                        symbol=spec.code,
                        period=period,
                        start_date=self._datetime_str(start),
                        end_date=self._datetime_str(end, end=True),
                        adjust=adjust if adjust in {"", "qfq", "hfq"} else "qfq",
                    )
        except Exception as exc:
            raise MarketDataError(f"AKShare {spec.kind} request failed for {spec.key}: {exc}") from exc
        return _normalize_history(raw, spec, self.name)


class BaostockBenchmarkProvider:
    """Independent BaoStock daily reference fallback for indices and ETFs."""

    name = "baostock-reference"

    def __init__(self, client=None):
        self.client = client or BaostockHistoryProvider()

    def history(
        self,
        benchmark: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        spec = resolve_reference_asset(benchmark)
        if interval not in {"1d", "day"}:
            raise NoMarketData("BaoStock reference fallback is daily-only; index minute data is not claimed")
        source_code = BAOSTOCK_REFERENCE_CODES.get(spec.key)
        if not source_code:
            raise NoMarketData(f"No explicit BaoStock source code registered for {spec.key}")
        raw = self.client._query_source_code(
            source_code,
            start,
            end,
            interval="1d",
            adjust="none" if spec.kind == "index" else adjust,
        )
        out = _normalize_history(raw, spec, self.name)
        out.attrs["baostock_code"] = source_code
        return out


class DemoBenchmarkProvider:
    name = "demo-reference"

    def history(
        self,
        benchmark: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        spec = resolve_reference_asset(benchmark)
        df = DemoProvider().history(spec.code, start, end, interval=interval, adjust=adjust)
        df.attrs.update(
            {
                "provider": self.name,
                "benchmark": spec.key,
                "reference_asset": spec.key,
                "name": f"DEMO-{spec.name}",
                "code": spec.code,
                "kind": spec.kind,
                "market": spec.market,
            }
        )
        return df


class BenchmarkProviderChain:
    name = "reference-chain"

    def __init__(self, providers=None):
        self.providers = providers or [EastmoneyBenchmarkProvider(), AkshareBenchmarkProvider()]

    def history(
        self,
        benchmark: str,
        start: date | str,
        end: date | str,
        interval: str = "1d",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        errors = []
        no_data = []
        for provider in self.providers:
            try:
                return provider.history(benchmark, start, end, interval=interval, adjust=adjust)
            except NoMarketData as exc:
                no_data.append(f"{getattr(provider, 'name', type(provider).__name__)}: {exc}")
            except Exception as exc:
                errors.append(f"{getattr(provider, 'name', type(provider).__name__)}: {exc}")
        if no_data:
            raise NoMarketData("No reference rows from available providers: " + " | ".join(no_data + errors))
        raise MarketDataError("All reference providers failed: " + " | ".join(errors))


def make_benchmark_provider(name: str):
    if name == "demo":
        return DemoBenchmarkProvider()
    if name == "eastmoney":
        return EastmoneyBenchmarkProvider()
    if name == "akshare":
        return AkshareBenchmarkProvider()
    if name == "auto":
        return BenchmarkProviderChain()
    raise ValueError(name)
