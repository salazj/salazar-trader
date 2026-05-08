"""Stock feature engine — computes ``StockFeatures`` from bars and quotes.

Indicators implemented locally (no external talib dependency, so this works
out of the box on Jetson Orin Nano):

* EMA-9 / EMA-21 / EMA-50
* SMA-20
* RSI-14
* MACD (12/26/9)
* VWAP and distance-from-VWAP
* ATR-14 (Wilder)
* Bollinger Bands (20, 2σ) + %B
* Volume surge ratio and relative volume
* Multi-timeframe momentum (1m/5m/15m)
* Trend strength (EMA slope) and volatility score (ATR / price)
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone

from app.monitoring import get_logger
from app.stocks.models import StockBar, StockFeatures
from app.utils.helpers import utc_now

logger = get_logger(__name__)


class StockFeatureEngine:
    """Maintains rolling windows of bars and computes technical features."""

    def __init__(self, symbol: str, max_bars: int = 250) -> None:
        self._symbol = symbol
        self._bars: deque[StockBar] = deque(maxlen=max_bars)
        self._last_quote: dict[str, float] = {}
        self._high_of_day: float = 0.0
        self._low_of_day: float = float("inf")
        self._day_volume: int = 0
        self._day_start: datetime | None = None

    # ── Mutation ───────────────────────────────────────────────────────

    def add_bar(self, bar: StockBar) -> None:
        self._bars.append(bar)
        if bar.high > self._high_of_day:
            self._high_of_day = bar.high
        if bar.low < self._low_of_day:
            self._low_of_day = bar.low
        self._day_volume += bar.volume

    def update_quote(self, bid: float, ask: float, last: float) -> None:
        self._last_quote = {"bid": bid, "ask": ask, "last": last}

    def start_new_day(self) -> None:
        self._high_of_day = 0.0
        self._low_of_day = float("inf")
        self._day_volume = 0
        self._day_start = utc_now()

    # ── Compute ────────────────────────────────────────────────────────

    def compute(self) -> StockFeatures:
        now = utc_now()
        closes = [b.close for b in self._bars]
        highs = [b.high for b in self._bars]
        lows = [b.low for b in self._bars]
        volumes = [b.volume for b in self._bars]

        last = self._last_quote.get("last", closes[-1] if closes else 0.0)
        bid = self._last_quote.get("bid", 0.0)
        ask = self._last_quote.get("ask", 0.0)

        ema_9 = self._ema(closes, 9)
        ema_21 = self._ema(closes, 21)
        ema_50 = self._ema(closes, 50)
        rsi_14 = self._compute_rsi(closes, 14)
        atr_14 = self._compute_atr(highs, lows, closes, 14)
        vwap = self._compute_vwap()

        macd_line, macd_signal, macd_hist = self._compute_macd(closes)
        bb_upper, bb_middle, bb_lower, bb_pct_b = self._compute_bbands(closes, 20, 2.0)

        relative_volume = self._compute_relative_volume(volumes)
        volume_surge_ratio = self._compute_volume_surge(volumes)

        distance_from_vwap_pct = (
            (last - vwap) / vwap * 100.0 if vwap > 0 else 0.0
        )

        volatility_score = atr_14 / last if last > 0 else 0.0
        trend_strength = self._compute_trend_strength(ema_9, ema_21, ema_50)

        return StockFeatures(
            symbol=self._symbol,
            timestamp=now,
            last_price=last,
            bid=bid,
            ask=ask,
            spread=ask - bid if bid > 0 and ask > 0 else 0.0,
            volume_1m=volumes[-1] if volumes else 0,
            volume_5m=sum(volumes[-5:]) if len(volumes) >= 5 else sum(volumes),
            volume_today=self._day_volume,
            relative_volume=relative_volume,
            volume_surge_ratio=volume_surge_ratio,
            vwap=vwap,
            price_vs_vwap=last - vwap if last > 0 else 0.0,
            distance_from_vwap_pct=distance_from_vwap_pct,
            high_of_day=self._high_of_day if self._high_of_day > 0 else last,
            low_of_day=self._low_of_day if self._low_of_day < float("inf") else last,
            sma_20=self._sma(closes, 20),
            ema_9=ema_9,
            ema_21=ema_21,
            ema_50=ema_50,
            trend_strength=trend_strength,
            rsi_14=rsi_14,
            macd_line=macd_line,
            macd_signal=macd_signal,
            macd_hist=macd_hist,
            atr_14=atr_14,
            volatility_1h=self._compute_volatility(closes, 60),
            volatility_score=volatility_score,
            bb_upper=bb_upper,
            bb_middle=bb_middle,
            bb_lower=bb_lower,
            bb_pct_b=bb_pct_b,
            momentum_1m=self._momentum(closes, 1),
            momentum_5m=self._momentum(closes, 5),
            momentum_15m=self._momentum(closes, 15),
        )

    # ── Indicator primitives ───────────────────────────────────────────

    def _compute_vwap(self) -> float:
        if not self._bars:
            return 0.0
        total_pv = sum(b.close * b.volume for b in self._bars)
        total_v = sum(b.volume for b in self._bars)
        return total_pv / total_v if total_v > 0 else 0.0

    @staticmethod
    def _compute_rsi(closes: list[float], period: int) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        recent = deltas[-period:]
        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]
        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _sma(values: list[float], period: int) -> float:
        if len(values) < period:
            return values[-1] if values else 0.0
        return sum(values[-period:]) / period

    @staticmethod
    def _ema(values: list[float], period: int) -> float:
        if not values:
            return 0.0
        if len(values) < period:
            return sum(values) / len(values)
        k = 2.0 / (period + 1)
        ema = values[0]
        for v in values[1:]:
            ema = v * k + ema * (1 - k)
        return ema

    @classmethod
    def _ema_series(cls, values: list[float], period: int) -> list[float]:
        if not values:
            return []
        k = 2.0 / (period + 1)
        out = [values[0]]
        for v in values[1:]:
            out.append(v * k + out[-1] * (1 - k))
        return out

    @classmethod
    def _compute_macd(
        cls, closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[float, float, float]:
        if len(closes) < slow + signal:
            return 0.0, 0.0, 0.0
        ema_fast = cls._ema_series(closes, fast)
        ema_slow = cls._ema_series(closes, slow)
        macd_series = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_series = cls._ema_series(macd_series, signal)
        line = macd_series[-1]
        sig = signal_series[-1]
        return line, sig, line - sig

    @staticmethod
    def _compute_bbands(
        values: list[float], period: int = 20, k: float = 2.0
    ) -> tuple[float, float, float, float]:
        if len(values) < period:
            mid = sum(values) / len(values) if values else 0.0
            return mid, mid, mid, 0.5
        window = values[-period:]
        mid = sum(window) / period
        var = sum((v - mid) ** 2 for v in window) / period
        std = math.sqrt(var)
        upper = mid + k * std
        lower = mid - k * std
        last = values[-1]
        denom = (upper - lower) or 1.0
        pct_b = (last - lower) / denom
        return upper, mid, lower, max(0.0, min(1.0, pct_b))

    @staticmethod
    def _compute_atr(
        highs: list[float], lows: list[float], closes: list[float], period: int
    ) -> float:
        if len(closes) < 2:
            return 0.0
        trs: list[float] = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 0.0
        return sum(trs[-period:]) / period

    @staticmethod
    def _compute_volatility(closes: list[float], window: int) -> float:
        if len(closes) < 2:
            return 0.0
        recent = closes[-window:]
        if len(recent) < 2:
            return 0.0
        mean = sum(recent) / len(recent)
        variance = sum((c - mean) ** 2 for c in recent) / len(recent)
        return variance ** 0.5

    @staticmethod
    def _compute_relative_volume(volumes: list[int]) -> float:
        if len(volumes) < 5:
            return 1.0
        recent = volumes[-1]
        baseline = sum(volumes[-20:]) / min(len(volumes), 20)
        if baseline <= 0:
            return 1.0
        return recent / baseline

    @staticmethod
    def _compute_volume_surge(volumes: list[int]) -> float:
        """Last-bar volume vs the 5-bar prior-window average."""
        if len(volumes) < 6:
            return 1.0
        recent = volumes[-1]
        prior = sum(volumes[-6:-1]) / 5.0
        if prior <= 0:
            return 1.0
        return recent / prior

    @staticmethod
    def _momentum(closes: list[float], lookback: int) -> float:
        if len(closes) <= lookback:
            return 0.0
        prev = closes[-lookback - 1]
        if prev == 0:
            return 0.0
        return (closes[-1] - prev) / prev

    @staticmethod
    def _compute_trend_strength(ema9: float, ema21: float, ema50: float) -> float:
        """Trend strength in [-1, 1] based on EMA stack alignment."""
        if ema50 <= 0:
            return 0.0
        if ema9 > ema21 > ema50:
            return min(1.0, (ema9 - ema50) / ema50 * 50.0)
        if ema9 < ema21 < ema50:
            return max(-1.0, (ema9 - ema50) / ema50 * 50.0)
        return 0.0
