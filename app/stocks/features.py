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
from datetime import date, datetime, timedelta, timezone

from app.monitoring import get_logger
from app.stocks.models import StockBar, StockFeatures
from app.utils.helpers import utc_now

logger = get_logger(__name__)

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback if tzdata missing
    _ET = timezone(timedelta(hours=-5))


def _et_date(ts: datetime) -> date:
    """ET calendar date for a timestamp (used to anchor the trading session)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(_ET).date()


class StockFeatureEngine:
    """Maintains rolling windows of bars and computes technical features."""

    def __init__(self, symbol: str, max_bars: int = 250) -> None:
        self._symbol = symbol
        self._bars: deque[StockBar] = deque(maxlen=max_bars)
        self._last_quote: dict[str, float] = {}
        self._high_of_day: float = 0.0
        self._low_of_day: float = float("inf")
        self._day_volume: int = 0
        self._session_date: date | None = None
        # Session-anchored VWAP accumulators (typical price × volume).
        self._session_pv: float = 0.0
        self._session_v: int = 0
        # Gap tracking: prior-session close and current-session open.
        self._prev_close: float = 0.0
        self._session_open: float = 0.0

    # ── Mutation ───────────────────────────────────────────────────────

    def add_bar(self, bar: StockBar) -> None:
        # Dedup / ordering guard: warmup (REST) and the live stream can both
        # deliver the same minute. Ignore bars at or before the latest one so
        # volume / VWAP / high-low aren't double-counted.
        if self._bars and bar.timestamp <= self._bars[-1].timestamp:
            return

        # Auto-roll the session on a new ET calendar day so VWAP / high-low /
        # volume re-anchor to the current session (the bar history deque is kept
        # intact so EMA/MACD stay warm across the open).
        bar_date = _et_date(bar.timestamp)
        if self._session_date is None or bar_date != self._session_date:
            # Remember the prior session's last close for gap computation.
            if self._bars:
                self._prev_close = self._bars[-1].close
            self._reset_session(bar_date)
            self._session_open = bar.open

        self._bars.append(bar)
        typical = (bar.high + bar.low + bar.close) / 3.0
        self._session_pv += typical * bar.volume
        self._session_v += bar.volume
        if bar.high > self._high_of_day:
            self._high_of_day = bar.high
        if bar.low < self._low_of_day:
            self._low_of_day = bar.low
        self._day_volume += bar.volume

    def update_quote(self, bid: float, ask: float, last: float) -> None:
        self._last_quote = {"bid": bid, "ask": ask, "last": last}

    def _reset_session(self, session_date: date | None) -> None:
        self._session_date = session_date
        self._session_pv = 0.0
        self._session_v = 0
        self._high_of_day = 0.0
        self._low_of_day = float("inf")
        self._day_volume = 0

    def start_new_day(self) -> None:
        self._reset_session(_et_date(utc_now()))

    # ── Compute ────────────────────────────────────────────────────────

    def compute(self) -> StockFeatures:
        # Stamp features with the latest bar time so the risk manager's data
        # freshness gate actually reflects market-data age (not wall clock).
        now = self._bars[-1].timestamp if self._bars else utc_now()
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

        gap_pct = (
            (self._session_open - self._prev_close) / self._prev_close * 100.0
            if self._prev_close > 0 and self._session_open > 0
            else 0.0
        )

        # Higher-timeframe (5-minute) context aggregated from the 1m bars.
        htf_closes, _htf_highs, _htf_lows = self._resample(5)
        htf_ema_9 = self._ema(htf_closes, 9)
        htf_ema_21 = self._ema(htf_closes, 21)
        htf_trend = self._ema_alignment(htf_ema_9, htf_ema_21)
        htf_rsi = self._compute_rsi(htf_closes, 14) if len(htf_closes) > 14 else 50.0

        mom_1m = self._momentum(closes, 1)
        mom_5m = self._momentum(closes, 5)
        mom_15m = self._momentum(closes, 15)
        mtf_alignment = self._mtf_alignment(mom_1m, mom_5m, mom_15m)

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
            momentum_1m=mom_1m,
            momentum_5m=mom_5m,
            momentum_15m=mom_15m,
            htf_trend=htf_trend,
            htf_rsi=htf_rsi,
            mtf_alignment=mtf_alignment,
            prev_close=self._prev_close,
            gap_pct=gap_pct,
        )

    def recent_return(self, lookback_bars: int) -> float:
        """Return over the last ``lookback_bars`` 1-minute closes (fraction)."""
        if len(self._bars) <= lookback_bars:
            return 0.0
        prev = self._bars[-lookback_bars - 1].close
        if prev <= 0:
            return 0.0
        return (self._bars[-1].close - prev) / prev

    # ── Multi-timeframe aggregation ────────────────────────────────────

    def _resample(self, period_min: int) -> tuple[list[float], list[float], list[float]]:
        """Aggregate the 1-minute bar history into ``period_min`` OHLC buckets.

        Bars are bucketed by wall-clock time (epoch // period) so the boundaries
        are stable and deterministic regardless of gaps in the stream. Returns
        ``(closes, highs, lows)`` per completed/partial bucket in chronological
        order — the last bucket may be in-progress, which is fine for a trend
        read.
        """
        if not self._bars:
            return [], [], []
        bucket_seconds = period_min * 60
        closes: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        cur_key: int | None = None
        hi = lo = cl = 0.0
        for b in self._bars:
            ts = b.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            key = int(ts.timestamp() // bucket_seconds)
            if key != cur_key:
                if cur_key is not None:
                    closes.append(cl)
                    highs.append(hi)
                    lows.append(lo)
                cur_key = key
                hi, lo, cl = b.high, b.low, b.close
            else:
                hi = max(hi, b.high)
                lo = min(lo, b.low)
                cl = b.close
        if cur_key is not None:
            closes.append(cl)
            highs.append(hi)
            lows.append(lo)
        return closes, highs, lows

    @staticmethod
    def _ema_alignment(fast: float, slow: float) -> float:
        """Normalize an EMA fast/slow spread into a [-1, 1] trend reading."""
        if slow <= 0:
            return 0.0
        diff = (fast - slow) / slow
        return max(-1.0, min(1.0, diff * 100.0))

    @staticmethod
    def _mtf_alignment(m1: float, m5: float, m15: float) -> float:
        """Agreement of 1m/5m/15m momentum direction in [-1, 1]."""
        signs = [1 if m > 0 else (-1 if m < 0 else 0) for m in (m1, m5, m15)]
        return sum(signs) / 3.0

    # ── Indicator primitives ───────────────────────────────────────────

    def _compute_vwap(self) -> float:
        """Session-anchored VWAP (typical price × volume since the open)."""
        if self._session_v > 0:
            return self._session_pv / self._session_v
        # Fallback before any session volume: last close.
        return self._bars[-1].close if self._bars else 0.0

    @staticmethod
    def _compute_rsi(closes: list[float], period: int) -> float:
        """Wilder-smoothed RSI."""
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]
        # Seed with the simple average of the first `period`, then Wilder-smooth.
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
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
        """Wilder-smoothed ATR."""
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
        atr = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            atr = (atr * (period - 1) + trs[i]) / period
        return atr

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
