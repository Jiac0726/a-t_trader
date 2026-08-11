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


def _clean_universe(stocks: pd.DataFrame, exclude_st: bool) -> pd.DataFrame:
    if stocks is None or stocks.empty:
        return pd.DataFrame()
    out = stocks.copy()
    out["code"] = out["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    if exclude_st and "name" in out.columns:
        out = out[~out["name"].astype(str).str.upper().str.contains("ST", na=False)]
    return out.drop_duplicates("code", keep="last").reset_index(drop=True)


def select_universe(
    provider: MarketDataProvider,
    limit: int | None = None,
    exclude_st: bool = True,
    min_spot_amount: float = 0.0,
) -> pd.DataFrame:
    stocks = _clean_universe(provider.stock_list(), exclude_st)
    if stocks.empty:
        return pd.DataFrame()
    if min_spot_amount > 0:
        if "amount" not in stocks.columns:
            raise ValueError("min_spot_amount requires a universe provider with real-time amount data")
        amount = pd.to_numeric(stocks["amount"], errors="coerce").fillna(0)
        stocks = stocks[amount >= min_spot_amount]
    if "amount" in stocks.columns:
        stocks = stocks.sort_values("amount", ascending=False, na_position="last")
    if limit:
        stocks = stocks.head(limit)
    return stocks.reset_index(drop=True)


def select_rankable_universe(
    provider: MarketDataProvider,
    *,
    store: DuckDBStore | None = None,
    limit: int | None = 100,
    exclude_st: bool = True,
    min_spot_amount: float = 0.0,
) -> pd.DataFrame:
    """Select a meaningful first-stage universe for the user-facing ranking.

    Prefer current amount when the upstream provides it. If the identity source
    has no amount field, fall back to the most recent locally cached T Score
    ranking. Never silently take the first N security codes and present them as
    a market-wide candidate screen.
    """
    stocks = _clean_universe(provider.stock_list(), exclude_st)
    if stocks.empty:
        return pd.DataFrame()

    has_amount = "amount" in stocks.columns and pd.to_numeric(stocks["amount"], errors="coerce").fillna(0).gt(0).any()
    if has_amount:
        stocks["amount"] = pd.to_numeric(stocks["amount"], errors="coerce").fillna(0)
        if min_spot_amount > 0:
            stocks = stocks[stocks["amount"] >= float(min_spot_amount)]
        stocks = stocks.sort_values(["amount", "code"], ascending=[False, True])
        basis = "current_amount"
    else:
        if min_spot_amount > 0:
            raise ValueError("当前股票池数据源没有实时成交额，无法应用成交额预筛。")
        cached = store.load_scores() if store is not None else pd.DataFrame()
        if cached is not None and not cached.empty and {"code", "score"}.issubset(cached.columns):
            cached = cached.copy()
            cached["code"] = cached["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
            cached["score"] = pd.to_numeric(cached["score"], errors="coerce").fillna(0)
            if "median_amount" not in cached.columns:
                cached["median_amount"] = 0.0
            cached["median_amount"] = pd.to_numeric(cached["median_amount"], errors="coerce").fillna(0)
            cached = cached[cached["score"] > 0].sort_values(["score", "median_amount"], ascending=[False, False])
            ranked_codes = cached[["code", "score", "median_amount"]].drop_duplicates("code")
            stocks = stocks.merge(ranked_codes, on="code", how="inner")
            if stocks.empty:
                raise ValueError("当前股票池没有实时成交额，本地历史评分缓存也无法与当前股票池匹配。")
            stocks = stocks.sort_values(["score", "median_amount", "code"], ascending=[False, False, True])
            basis = "cached_t_score"
        elif limit is not None and int(limit) < len(stocks):
            raise ValueError(
                "当前股票池数据源没有实时成交额，而且本地还没有可复用的历史评分。"
                "首次全市场粗筛不能按股票代码顺序随便截取前N只；请切换可提供成交额的数据源，"
                "或先用自选池建立评分缓存。"
            )
        else:
            basis = "full_universe_no_prefilter"

    if limit:
        stocks = stocks.head(int(limit))
    out = stocks.reset_index(drop=True)
    out.attrs["selection_basis"] = basis
    return out


def scan_universe(
    provider: MarketDataProvider,
    limit: int | None = None,
    exclude_st: bool = True,
    min_spot_amount: float = 0.0,
    **scan_kwargs,
) -> pd.DataFrame:
    stocks = select_universe(
        provider,
        limit=limit,
        exclude_st=exclude_st,
        min_spot_amount=min_spot_amount,
    )
    if stocks.empty:
        return pd.DataFrame()
    return scan_codes(stocks["code"].tolist(), provider, **scan_kwargs)
