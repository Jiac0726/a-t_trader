from __future__ import annotations

from datetime import date, timedelta
import pandas as pd

from data.validator import validate_ohlcv
from features.daily import daily_features
from providers.base import MarketDataProvider
from scoring.t_score import build_t_score
from storage.duckdb_store import DuckDBStore


def scan_codes(
    codes: list[str],
    provider: MarketDataProvider,
    end: date | None = None,
    calendar_days: int = 140,
    lookback: int = 60,
    store: DuckDBStore | None = None,
) -> pd.DataFrame:
    end = end or date.today()
    start = end - timedelta(days=calendar_days)
    results = []
    for raw_code in codes:
        code = str(raw_code).strip().zfill(6)
        if not code or code == "000000":
            continue
        try:
            df = provider.history(code, start, end, interval="1d", adjust="qfq")
            name = df.attrs.get("name", "")
            provider_name = df.attrs.get("provider", provider.name)
            clean = validate_ohlcv(df)
            features = daily_features(clean, lookback=lookback)
            result = build_t_score(code, name, features, provider=provider_name)
        except Exception as exc:
            result = build_t_score(
                code,
                "",
                {
                    "avg_amplitude": 0,
                    "median_amount": 0,
                    "avg_intraday_space": 0,
                    "reversal_rate": 0,
                    "trend_distance": 99,
                    "max_drawdown": -100,
                    "extreme_day_rate": 100,
                    "return_volatility": 100,
                    "latest_close": 0,
                    "observations": 0,
                },
                provider=provider.name,
            )
            result.score = 0
            result.grade = "ERR"
            result.error = str(exc)
        results.append(result.to_dict())
    if not results:
        return pd.DataFrame()
    out = pd.DataFrame(results).sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)
    if store is not None:
        store.save_scores(out)
    return out


def scan_universe(
    provider: MarketDataProvider,
    limit: int | None = None,
    exclude_st: bool = True,
    min_spot_amount: float = 0.0,
    **scan_kwargs,
) -> pd.DataFrame:
    stocks = provider.stock_list().copy()
    if stocks.empty:
        return pd.DataFrame()
    if exclude_st and "name" in stocks.columns:
        stocks = stocks[~stocks["name"].astype(str).str.upper().str.contains("ST")]
    if min_spot_amount > 0 and "amount" in stocks.columns:
        amount = pd.to_numeric(stocks["amount"], errors="coerce").fillna(0)
        stocks = stocks[amount >= min_spot_amount]
    if "amount" in stocks.columns:
        stocks = stocks.sort_values("amount", ascending=False, na_position="last")
    if limit:
        stocks = stocks.head(limit)
    return scan_codes(stocks["code"].tolist(), provider, **scan_kwargs)
