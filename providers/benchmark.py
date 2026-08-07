from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from .base import MarketDataError, NoMarketData
from .eastmoney import EastmoneyProvider


@dataclass(frozen=True, slots=True)
class BenchmarkSpec:
    key: str
    code: str
    name: str
    market: str
    eastmoney_secid: str
    akshare_symbol: str
    kind: str = "index"


BENCHMARKS: dict[str, BenchmarkSpec] = {
    "sse": BenchmarkSpec("sse", "000001", "上证指数", "SH", "1.000001", "sh000001"),
    "csi300": BenchmarkSpec("csi300", "000300", "沪深300", "CSI", "1.000300", "csi000300"),
    "csi500": BenchmarkSpec("csi500", "000905", "中证500", "CSI", "1.000905", "csi000905"),
    "csi1000": BenchmarkSpec("csi1000", "000852", "中证1000", "CSI", "1.000852", "csi000852"),
    "szse": BenchmarkSpec("szse", "399001", "深证成指", "SZ", "0.399001", "sz399001"),
    "chinext": BenchmarkSpec("chinext", "399006", "创业板指", "SZ", "0.399006", "sz399006"),
    "star50": BenchmarkSpec("star50", "000688", "科创50", "SH", "1.000688", "sh000688"),
}


def resolve_benchmark(value: str) -> BenchmarkSpec:
    raw = str(value).strip().lower()
    if raw in BENCHMARKS:
        return BENCHMARKS[raw]
    matches = [spec for spec in BENCHMARKS.values() if spec.code == raw]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"Unknown/ambiguous benchmark: {value}. Use one of: {', '.join(BENCHMARKS)}")


class EastmoneyBenchmarkProvider:
    name = "eastmoney-benchmark"

    def __init__(self, timeout: float = 12.0, min_interval: float = 0.35):
        self.client = EastmoneyProvider(timeout=timeout, min_interval=min_interval)

    def history(self, benchmark: str, start: date | str, end: date | str, interval: str = "1d") -> pd.DataFrame:
        spec = resolve_benchmark(benchmark)
        params: dict[str, Any] = {
            "secid": spec.eastmoney_secid,
            "klt": self.client._klt(interval),
            "fqt": "0",
            "beg": self.client._date_str(start),
            "end": self.client._date_str(end),
            "lmt": "1000000",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        payload = self.client._get_json(self.client._HISTORY_URL, params)
        data = payload.get("data") or {}
        klines = data.get("klines") or []
        if not klines:
            raise NoMarketData(f"Eastmoney returned no benchmark data for {spec.key}")
        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 7:
                continue
            rows.append({
                "datetime": parts[0], "open": parts[1], "close": parts[2], "high": parts[3], "low": parts[4],
                "volume": parts[5], "amount": parts[6],
                "amplitude": parts[7] if len(parts) > 7 else None,
                "pct_change": parts[8] if len(parts) > 8 else None,
            })
        df = pd.DataFrame(rows)
        for c in ["open", "close", "high", "low", "volume", "amount", "amplitude", "pct_change"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
        df.attrs.update({"provider": self.name, "benchmark": spec.key, "name": spec.name, "code": spec.code, "kind": spec.kind})
        return df


class AkshareBenchmarkProvider:
    name = "akshare-benchmark"

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

    def history(self, benchmark: str, start: date | str, end: date | str, interval: str = "1d") -> pd.DataFrame:
        if interval != "1d":
            raise MarketDataError("AKShare benchmark fallback currently supports daily history only")
        spec = resolve_benchmark(benchmark)
        ak = self._ak()
        try:
            raw = ak.stock_zh_index_daily_em(symbol=spec.akshare_symbol, start_date=self._date_str(start), end_date=self._date_str(end))
        except Exception as exc:
            raise MarketDataError(f"AKShare benchmark request failed for {spec.key}: {exc}") from exc
        if raw is None or raw.empty:
            raise NoMarketData(f"AKShare returned no benchmark data for {spec.key}")
        df = raw.rename(columns={"date": "datetime"}).copy()
        for c in ["open", "close", "high", "low", "volume", "amount"]:
            if c not in df.columns:
                if c == "amount":
                    df[c] = 0.0
                else:
                    raise MarketDataError(f"AKShare benchmark schema changed; missing {c}")
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
        df.attrs.update({"provider": self.name, "benchmark": spec.key, "name": spec.name, "code": spec.code, "kind": spec.kind})
        return df


class BenchmarkProviderChain:
    name = "benchmark-chain"

    def __init__(self, providers=None):
        self.providers = providers or [EastmoneyBenchmarkProvider(), AkshareBenchmarkProvider()]

    def history(self, benchmark: str, start: date | str, end: date | str, interval: str = "1d") -> pd.DataFrame:
        errors = []
        for p in self.providers:
            try:
                return p.history(benchmark, start, end, interval=interval)
            except Exception as exc:
                errors.append(f"{getattr(p, 'name', type(p).__name__)}: {exc}")
        raise MarketDataError("All benchmark providers failed: " + " | ".join(errors))
