from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(slots=True)
class TScoreResult:
    code: str
    name: str = ""
    score: float = 0.0
    grade: str = "C"
    amplitude_score: float = 0.0
    liquidity_score: float = 0.0
    tradable_space_score: float = 0.0
    mean_reversion_score: float = 0.0
    trend_score: float = 0.0
    risk_score: float = 0.0
    avg_amplitude: float = 0.0
    median_amount: float = 0.0
    avg_intraday_space: float = 0.0
    max_drawdown: float = 0.0
    latest_close: float = 0.0
    observations: int = 0
    provider: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
