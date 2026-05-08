"""Pullback strategy.

Idea: in an established uptrend (EMA-9 > EMA-21 > EMA-50), wait for a
short-term pullback to the 21-EMA (or VWAP) with cooling RSI, then enter
long only if the bar shows bounce confirmation.

Required indicators: EMA-9, EMA-21, EMA-50, RSI-14, VWAP, ATR-14.
"""

from __future__ import annotations

from app.data.models import PortfolioSnapshot
from app.models.enums import OrderType, StockAction
from app.stocks.models import StockFeatures, StockSignal
from app.stocks.strategies.base import BaseStockStrategy


class StockPullback(BaseStockStrategy):
    name = "stock_pullback"

    RSI_COOLED_MAX = 55.0
    RSI_BOUNCE_MIN = 35.0
    PULLBACK_TOLERANCE = 0.012  # within 1.2% of EMA-21 / VWAP
    ATR_STOP_MULTIPLIER = 1.5
    ATR_TARGET_MULTIPLIER = 2.5

    def generate_signal(
        self, features: StockFeatures, portfolio: PortfolioSnapshot
    ) -> StockSignal | None:
        if features.last_price <= 0 or features.atr_14 <= 0:
            return None
        if features.ema_9 <= 0 or features.ema_21 <= 0 or features.ema_50 <= 0:
            return None

        uptrend = (
            features.ema_9 > features.ema_21 > features.ema_50
            and features.trend_strength > 0
        )
        if not uptrend:
            return None

        ref_level = features.ema_21
        if features.vwap > 0 and features.vwap > features.ema_50:
            ref_level = max(features.ema_21, features.vwap)

        distance = abs(features.last_price - ref_level) / max(ref_level, 1e-6)
        in_pullback_zone = distance <= self.PULLBACK_TOLERANCE

        rsi_cooled = self.RSI_BOUNCE_MIN <= features.rsi_14 <= self.RSI_COOLED_MAX

        bounce_confirmation = (
            features.last_price > features.ema_21
            and features.momentum_1m > 0
        )

        if in_pullback_zone and rsi_cooled and bounce_confirmation:
            stop = features.last_price - features.atr_14 * self.ATR_STOP_MULTIPLIER
            target = features.last_price + features.atr_14 * self.ATR_TARGET_MULTIPLIER
            confidence = min(
                0.85,
                0.45 + features.trend_strength * 0.3 + (1.0 - distance / self.PULLBACK_TOLERANCE) * 0.1,
            )
            return StockSignal(
                strategy_name=self.name,
                symbol=features.symbol,
                action=StockAction.BUY,
                confidence=confidence,
                suggested_price=features.last_price,
                order_type=OrderType.LIMIT,
                stop_price=stop,
                rationale=(
                    f"Uptrend pullback to {ref_level:.2f}, "
                    f"RSI={features.rsi_14:.1f}, ATR stop={stop:.2f}, "
                    f"target={target:.2f}"
                ),
            )

        return None
