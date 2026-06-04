"""Momentum strategy for stocks.

Conditions for a BUY:
* EMA-9 > EMA-21 (short-term up) and price above VWAP
* RSI-14 below the overbought threshold
* 5-minute momentum positive and above ``MOMENTUM_THRESHOLD``
* Volume confirmation: ``volume_surge_ratio`` >= ``MIN_VOLUME_SURGE``
* Higher-timeframe confirmation: not fighting the 5-minute trend
  (``htf_trend`` >= 0), avoiding longs into a higher-timeframe downtrend
* ATR-based stop loss attached to the signal
"""

from __future__ import annotations

from app.data.models import PortfolioSnapshot
from app.models.enums import OrderType, StockAction
from app.stocks.models import StockFeatures, StockSignal
from app.stocks.strategies.base import BaseStockStrategy


class StockMomentum(BaseStockStrategy):
    name = "stock_momentum"

    # Loosened for activity: a ~0.15%/5min thrust on roughly average volume is
    # enough to act on, instead of demanding a 0.3% move + 1.2x volume surge
    # (which almost never co-occur on a quiet tape).
    MOMENTUM_THRESHOLD = 0.0015
    RSI_OVERBOUGHT = 72.0
    RSI_OVERSOLD = 30.0
    MIN_VOLUME_SURGE = 1.0
    ATR_STOP_MULTIPLIER = 1.5
    REWARD_RISK = 2.0
    COOLDOWN_BARS = 3

    def __init__(self) -> None:
        self._last_signal_bar: dict[str, int] = {}
        # Per-symbol tick counter so the cooldown is independent of how many
        # symbols share the loop (a single global counter made COOLDOWN_BARS
        # effectively num_symbols× shorter).
        self._bar_count: dict[str, int] = {}

    def generate_signal(
        self, features: StockFeatures, portfolio: PortfolioSnapshot
    ) -> StockSignal | None:
        symbol = features.symbol
        count = self._bar_count.get(symbol, 0) + 1
        self._bar_count[symbol] = count

        last_bar = self._last_signal_bar.get(symbol, -self.COOLDOWN_BARS)
        if count - last_bar < self.COOLDOWN_BARS:
            return None

        if features.last_price <= 0 or features.ema_9 <= 0:
            return None

        # Trend confirmation: short-term EMA above medium-term EMA (matches the
        # documented behavior and filters counter-trend longs).
        ema_uptrend = features.ema_21 <= 0 or features.ema_9 > features.ema_21
        price_above_ema = features.last_price > features.ema_9
        price_above_vwap = (
            features.vwap <= 0 or features.last_price >= features.vwap
        )
        momentum_strong = features.momentum_5m > self.MOMENTUM_THRESHOLD
        rsi_ok = features.rsi_14 < self.RSI_OVERBOUGHT
        volume_ok = (
            features.volume_surge_ratio >= self.MIN_VOLUME_SURGE
            or features.relative_volume >= self.MIN_VOLUME_SURGE
        )
        # Don't chase a 1m pop into a 5m downtrend. htf_trend is 0.0 until
        # enough bars exist, so this is permissive early in the session.
        htf_ok = features.htf_trend >= 0.0

        if (
            ema_uptrend and price_above_ema and price_above_vwap
            and momentum_strong and rsi_ok and volume_ok and htf_ok
        ):
            # Base confidence on 1m thrust, with a bonus when all three
            # timeframes (1m/5m/15m) agree on direction.
            confidence = min(0.9, 0.5 + features.momentum_5m * 10)
            if features.mtf_alignment > 0:
                confidence = min(0.95, confidence + 0.1 * features.mtf_alignment)
            self._last_signal_bar[symbol] = count
            last = features.last_price
            atr = features.atr_14 if features.atr_14 > 0 else last * 0.01
            stop = last - atr * self.ATR_STOP_MULTIPLIER
            target = last + self.REWARD_RISK * (last - stop)
            return StockSignal(
                strategy_name=self.name,
                symbol=symbol,
                action=StockAction.BUY,
                confidence=confidence,
                suggested_price=last,
                order_type=OrderType.LIMIT,
                stop_price=round(stop, 2),
                target_price=round(target, 2),
                rationale=(
                    f"EMA9>EMA21>VWAP, mom_5m={features.momentum_5m:.4f}, "
                    f"RSI={features.rsi_14:.1f}, "
                    f"vol_surge={features.volume_surge_ratio:.2f}, "
                    f"htf_trend={features.htf_trend:+.2f}, "
                    f"mtf_align={features.mtf_alignment:+.2f}"
                ),
            )

        momentum_reversed = features.momentum_5m < -self.MOMENTUM_THRESHOLD
        price_below_ema = features.last_price < features.ema_9

        if price_below_ema and momentum_reversed:
            confidence = min(0.8, 0.4 + abs(features.momentum_5m) * 10)
            self._last_signal_bar[symbol] = count
            return StockSignal(
                strategy_name=self.name,
                symbol=symbol,
                action=StockAction.SELL,
                confidence=confidence,
                suggested_price=features.last_price,
                order_type=OrderType.LIMIT,
                rationale=f"Momentum reversal, 5m momentum={features.momentum_5m:.4f}",
            )

        return None
