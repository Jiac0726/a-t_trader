from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import pandas as pd

Mode = Literal["positive", "reverse"]


@dataclass(slots=True)
class CostModel:
    """Configurable A-share transaction-cost model.

    `commission_rate` is the user's broker commission assumption. Some broker
    quotes already include exchange/regulatory charges, so `other_rate_per_side`
    defaults to zero to avoid double-counting. The default sell-side stamp duty
    is 0.05% (0.0005), reflecting the policy effective since 2023-08-28.
    """

    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_sell_rate: float = 0.0005
    other_rate_per_side: float = 0.0
    slippage_bps: float = 2.0

    def execution_price(self, market_price: float, side: Literal["buy", "sell"]) -> float:
        slip = self.slippage_bps / 10_000.0
        return float(market_price) * (1 + slip if side == "buy" else 1 - slip)

    def fees(self, execution_price: float, shares: int, side: Literal["buy", "sell"]) -> float:
        amount = float(execution_price) * int(shares)
        commission = max(self.min_commission, amount * self.commission_rate) if shares > 0 else 0.0
        other = amount * self.other_rate_per_side
        stamp = amount * self.stamp_duty_sell_rate if side == "sell" else 0.0
        return commission + other + stamp


@dataclass(slots=True)
class TTrade:
    date: str
    mode: Mode
    entry_time: str
    exit_time: str
    entry_market_price: float
    exit_market_price: float
    shares: int
    gross_pnl: float
    fees: float
    net_pnl: float
    temp_capital: float
    net_return_pct: float
    strategy: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class BacktestSummary:
    trades: int
    wins: int
    win_rate: float
    total_net_pnl: float
    avg_net_pnl: float
    avg_net_return_pct: float
    median_net_return_pct: float
    max_win: float
    max_loss: float

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_t_shares(bottom_shares: int, t_ratio: float, lot_size: int = 100) -> int:
    """Clamp T quantity to start-of-day sellable bottom shares (T+1 constraint)."""
    bottom = max(0, int(bottom_shares))
    ratio = max(0.0, min(1.0, float(t_ratio)))
    lot = max(1, int(lot_size))
    shares = int(bottom * ratio) // lot * lot
    return min(shares, bottom // lot * lot)


def _trade_from_pair(
    day: pd.DataFrame,
    mode: Mode,
    entry_i: int,
    exit_i: int,
    shares: int,
    costs: CostModel,
    strategy: str,
) -> TTrade:
    entry = day.iloc[entry_i]
    exit_ = day.iloc[exit_i]
    entry_market = float(entry["close"] if "execution_price" not in entry else entry["execution_price"])
    exit_market = float(exit_["close"] if "execution_price" not in exit_ else exit_["execution_price"])

    if mode == "positive":
        buy_market, sell_market = entry_market, exit_market
        buy_time, sell_time = pd.Timestamp(entry["datetime"]), pd.Timestamp(exit_["datetime"])
    else:
        sell_market, buy_market = entry_market, exit_market
        sell_time, buy_time = pd.Timestamp(entry["datetime"]), pd.Timestamp(exit_["datetime"])

    buy_exec = costs.execution_price(buy_market, "buy")
    sell_exec = costs.execution_price(sell_market, "sell")
    buy_fees = costs.fees(buy_exec, shares, "buy")
    sell_fees = costs.fees(sell_exec, shares, "sell")
    gross = (sell_exec - buy_exec) * shares
    total_fees = buy_fees + sell_fees
    net = gross - total_fees
    temp_capital = buy_exec * shares + buy_fees
    net_return = net / temp_capital * 100 if temp_capital > 0 else 0.0

    return TTrade(
        date=str(pd.Timestamp(day.iloc[0]["datetime"]).date()),
        mode=mode,
        entry_time=(buy_time if mode == "positive" else sell_time).strftime("%H:%M"),
        exit_time=(sell_time if mode == "positive" else buy_time).strftime("%H:%M"),
        entry_market_price=round(entry_market, 4),
        exit_market_price=round(exit_market, 4),
        shares=shares,
        gross_pnl=round(gross, 2),
        fees=round(total_fees, 2),
        net_pnl=round(net, 2),
        temp_capital=round(temp_capital, 2),
        net_return_pct=round(net_return, 4),
        strategy=strategy,
    )


def _best_pair(day: pd.DataFrame, mode: Mode) -> tuple[int, int] | None:
    """Hindsight pair using bar closes only; deliberately an opportunity ceiling."""
    prices = pd.to_numeric(day["close"], errors="coerce").to_numpy(dtype=float)
    if len(prices) < 2 or not np.isfinite(prices).all():
        return None
    best_pair: tuple[int, int] | None = None
    best_spread = 0.0
    if mode == "positive":
        min_i = 0
        for i in range(1, len(prices)):
            spread = prices[i] - prices[min_i]
            if spread > best_spread:
                best_spread = spread
                best_pair = (min_i, i)
            if prices[i] < prices[min_i]:
                min_i = i
    else:
        max_i = 0
        for i in range(1, len(prices)):
            spread = prices[max_i] - prices[i]
            if spread > best_spread:
                best_spread = spread
                best_pair = (max_i, i)
            if prices[i] > prices[max_i]:
                max_i = i
    return best_pair


def best_single_t_envelope(
    df: pd.DataFrame,
    mode: Mode,
    bottom_shares: int = 1000,
    t_ratio: float = 0.5,
    costs: CostModel | None = None,
    lot_size: int = 100,
) -> pd.DataFrame:
    """Daily hindsight upper bound for one T round-trip.

    This is NOT a tradable strategy because it selects the best pair after seeing
    the whole day. It answers a narrower product question: did the day contain
    enough ordered price movement to overcome realistic costs?
    """
    costs = costs or CostModel()
    shares = resolve_t_shares(bottom_shares, t_ratio, lot_size)
    if shares <= 0:
        return pd.DataFrame()
    x = df.copy().sort_values("datetime")
    x["datetime"] = pd.to_datetime(x["datetime"])
    x["date"] = x["datetime"].dt.date
    trades: list[dict] = []
    for _, day in x.groupby("date", sort=True):
        day = day.reset_index(drop=True)
        pair = _best_pair(day, mode)
        if pair is None:
            continue
        # With minimum commission and sell-side tax, the raw largest spread is
        # not guaranteed to be the largest net PnL. Brute-force all ordered
        # pairs (A-share sessions have few 5-minute bars) and keep the best net.
        best_trade: TTrade | None = None
        for entry_i in range(len(day) - 1):
            for exit_i in range(entry_i + 1, len(day)):
                candidate = _trade_from_pair(
                    day, mode, entry_i, exit_i, shares, costs, strategy="hindsight_envelope"
                )
                if best_trade is None or candidate.net_pnl > best_trade.net_pnl:
                    best_trade = candidate
        if best_trade is not None:
            trades.append(best_trade.to_dict())
    return pd.DataFrame(trades)


def _mean_reversion_day(
    day: pd.DataFrame,
    mode: Mode,
    shares: int,
    costs: CostModel,
    window: int,
    entry_z: float,
    exit_z: float,
) -> TTrade | None:
    day = day.sort_values("datetime").reset_index(drop=True).copy()
    if len(day) < window + 3:
        return None
    close = pd.to_numeric(day["close"], errors="coerce")
    mean = close.rolling(window, min_periods=window).mean()
    std = close.rolling(window, min_periods=window).std(ddof=0).replace(0, np.nan)
    z = (close - mean) / std

    entry_signal_i: int | None = None
    for i in range(window - 1, len(day) - 2):
        value = z.iloc[i]
        if not np.isfinite(value):
            continue
        if (mode == "positive" and value <= -abs(entry_z)) or (mode == "reverse" and value >= abs(entry_z)):
            entry_signal_i = i
            break
    if entry_signal_i is None:
        return None

    # Execute on the next bar close as a conservative, deterministic MVP proxy.
    # A future version can switch to next-bar open if the provider reliably supplies it.
    entry_i = entry_signal_i + 1
    exit_i: int | None = None
    for i in range(entry_i, len(day) - 1):
        value = z.iloc[i]
        if not np.isfinite(value):
            continue
        if (mode == "positive" and value >= exit_z) or (mode == "reverse" and value <= -exit_z):
            exit_i = i + 1
            break
    if exit_i is None or exit_i <= entry_i:
        exit_i = len(day) - 1
    if exit_i <= entry_i:
        return None
    return _trade_from_pair(day, mode, entry_i, exit_i, shares, costs, strategy="rolling_z_mean_reversion")


def mean_reversion_backtest(
    df: pd.DataFrame,
    mode: Mode,
    bottom_shares: int = 1000,
    t_ratio: float = 0.5,
    costs: CostModel | None = None,
    lot_size: int = 100,
    window: int = 6,
    entry_z: float = 1.0,
    exit_z: float = 0.0,
) -> pd.DataFrame:
    """Causal single-round daily baseline using rolling z-score signals.

    Signals only use current/past bars; execution is delayed by one bar. One
    round-trip is allowed per day. T quantity never exceeds start-of-day bottom
    shares, which models the A-share T+1 sellable-inventory constraint.
    """
    costs = costs or CostModel()
    shares = resolve_t_shares(bottom_shares, t_ratio, lot_size)
    if shares <= 0:
        return pd.DataFrame()
    x = df.copy().sort_values("datetime")
    x["datetime"] = pd.to_datetime(x["datetime"])
    x["date"] = x["datetime"].dt.date
    trades: list[dict] = []
    for _, day in x.groupby("date", sort=True):
        trade = _mean_reversion_day(
            day,
            mode=mode,
            shares=shares,
            costs=costs,
            window=max(3, int(window)),
            entry_z=float(entry_z),
            exit_z=float(exit_z),
        )
        if trade is not None:
            trades.append(trade.to_dict())
    return pd.DataFrame(trades)


def summarize_trades(trades: pd.DataFrame) -> BacktestSummary:
    if trades is None or trades.empty:
        return BacktestSummary(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    pnl = pd.to_numeric(trades["net_pnl"], errors="coerce").fillna(0.0)
    ret = pd.to_numeric(trades["net_return_pct"], errors="coerce").fillna(0.0)
    wins = int((pnl > 0).sum())
    return BacktestSummary(
        trades=len(trades),
        wins=wins,
        win_rate=round(wins / len(trades) * 100, 2),
        total_net_pnl=round(float(pnl.sum()), 2),
        avg_net_pnl=round(float(pnl.mean()), 2),
        avg_net_return_pct=round(float(ret.mean()), 4),
        median_net_return_pct=round(float(ret.median()), 4),
        max_win=round(float(pnl.max()), 2),
        max_loss=round(float(pnl.min()), 2),
    )
