"""Market regime detector.

Inputs:
    * Recent SPY closes (and optionally QQQ)
    * Recent ATR (or volatility) values
    * Optional VIX level

Output: ``RegimeReading`` describing the current regime with the
confidence and the inputs that drove the classification. Strategies and
the decision engine consult this to bias their trade selection (for
example, prefer momentum in trending bullish, prefer mean-reversion in
range-bound, reduce activity in risk-off / high-volatility).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from app.utils.helpers import utc_now


class MarketRegime(str, Enum):
    TRENDING_BULLISH = "trending_bullish"
    TRENDING_BEARISH = "trending_bearish"
    RANGE_BOUND = "range_bound"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    RISK_OFF = "risk_off"


@dataclass
class RegimeReading:
    regime: MarketRegime
    confidence: float
    score: float
    timestamp: datetime
    spy_trend: float = 0.0
    qqq_trend: float = 0.0
    atr_pct: float = 0.0
    vix: float | None = None
    volume_ratio: float = 1.0
    inputs: dict = field(default_factory=dict)
    rationale: str = ""

    @property
    def allows_long(self) -> bool:
        return self.regime not in {
            MarketRegime.TRENDING_BEARISH,
            MarketRegime.RISK_OFF,
        }

    @property
    def prefers_momentum(self) -> bool:
        return self.regime == MarketRegime.TRENDING_BULLISH

    @property
    def prefers_mean_reversion(self) -> bool:
        return self.regime in {MarketRegime.RANGE_BOUND, MarketRegime.LOW_VOLATILITY}

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "confidence": self.confidence,
            "score": self.score,
            "timestamp": self.timestamp.isoformat(),
            "spy_trend": self.spy_trend,
            "qqq_trend": self.qqq_trend,
            "atr_pct": self.atr_pct,
            "vix": self.vix,
            "volume_ratio": self.volume_ratio,
            "rationale": self.rationale,
            "allows_long": self.allows_long,
        }


def _trend(closes: Iterable[float], lookback: int = 20) -> float:
    closes = list(closes)
    if len(closes) < lookback + 1:
        return 0.0
    head = closes[-1]
    tail = closes[-lookback - 1]
    if tail <= 0:
        return 0.0
    return (head - tail) / tail


def classify_regime(
    spy_closes: list[float],
    *,
    qqq_closes: list[float] | None = None,
    atr_pct: float = 0.0,
    vix: float | None = None,
    volume_ratio: float = 1.0,
    lookback: int = 20,
) -> RegimeReading:
    """Stateless single-shot classifier returning a ``RegimeReading``."""
    spy_trend = _trend(spy_closes, lookback)
    qqq_trend = _trend(qqq_closes or [], lookback)

    avg_trend = spy_trend
    if qqq_closes:
        avg_trend = 0.5 * spy_trend + 0.5 * qqq_trend

    risk_off = (vix is not None and vix >= 30.0) or atr_pct >= 0.04

    if risk_off:
        regime = MarketRegime.RISK_OFF
        score = -1.0
        confidence = 0.85
        rationale = (
            f"VIX={vix} (>=30) or ATR%={atr_pct:.3f} (>=4%): risk-off"
        )
    elif atr_pct >= 0.025:
        regime = MarketRegime.HIGH_VOLATILITY
        score = avg_trend
        confidence = 0.7
        rationale = f"ATR%={atr_pct:.3f}: high volatility"
    elif avg_trend >= 0.02:
        regime = MarketRegime.TRENDING_BULLISH
        score = avg_trend
        confidence = min(0.9, 0.5 + abs(avg_trend) * 5)
        rationale = f"SPY/QQQ {lookback}-bar trend +{avg_trend:.2%}"
    elif avg_trend <= -0.02:
        regime = MarketRegime.TRENDING_BEARISH
        score = avg_trend
        confidence = min(0.9, 0.5 + abs(avg_trend) * 5)
        rationale = f"SPY/QQQ {lookback}-bar trend {avg_trend:.2%}"
    elif atr_pct <= 0.005 and abs(avg_trend) < 0.005:
        regime = MarketRegime.LOW_VOLATILITY
        score = 0.0
        confidence = 0.7
        rationale = f"ATR%={atr_pct:.3f}, trend flat"
    else:
        regime = MarketRegime.RANGE_BOUND
        score = avg_trend
        confidence = 0.6
        rationale = (
            f"trend {avg_trend:.2%} within +/-2%: range-bound"
        )

    return RegimeReading(
        regime=regime,
        confidence=confidence,
        score=score,
        timestamp=utc_now(),
        spy_trend=spy_trend,
        qqq_trend=qqq_trend,
        atr_pct=atr_pct,
        vix=vix,
        volume_ratio=volume_ratio,
        rationale=rationale,
        inputs={
            "spy_closes_n": len(spy_closes),
            "qqq_closes_n": len(qqq_closes) if qqq_closes else 0,
            "lookback": lookback,
        },
    )


class MarketRegimeDetector:
    """Stateful wrapper around ``classify_regime`` with rolling buffers."""

    def __init__(self, lookback: int = 20, history_size: int = 200) -> None:
        self._lookback = lookback
        self._spy: list[float] = []
        self._qqq: list[float] = []
        self._max = history_size
        self._last: RegimeReading | None = None

    def update_close(self, symbol: str, close: float) -> None:
        s = symbol.upper()
        if s == "SPY":
            self._spy.append(close)
            self._spy = self._spy[-self._max :]
        elif s == "QQQ":
            self._qqq.append(close)
            self._qqq = self._qqq[-self._max :]

    def evaluate(
        self,
        *,
        atr_pct: float = 0.0,
        vix: float | None = None,
        volume_ratio: float = 1.0,
    ) -> RegimeReading:
        reading = classify_regime(
            self._spy,
            qqq_closes=self._qqq,
            atr_pct=atr_pct,
            vix=vix,
            volume_ratio=volume_ratio,
            lookback=self._lookback,
        )
        self._last = reading
        return reading

    @property
    def last(self) -> RegimeReading | None:
        return self._last
