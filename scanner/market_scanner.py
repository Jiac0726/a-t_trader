from __future__ import annotations

from datetime import date, timedelta
import pandas as pd

from data.validator import validate_ohlcv
from features.daily import daily_features
from providers.base import MarketDataProvider
from scoring.t_score import build_t_score


def scan_codes(
    codes: list[str],
    provider: MarketDataProvider,
    end: date | None = None,
    calendar_days: int = 140,
    lookback: int = 60,
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
    return pd.DataFrame(results).sort_values(["score", "median_amount"], ascending=[False, False]).reset_index(drop=True)
