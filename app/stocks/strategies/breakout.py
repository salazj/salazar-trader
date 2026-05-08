"""Breakout strategy.

Conditions:
* Price reaches or exceeds the high of day
* Relative volume confirmation (volume surge or relative volume >= multiple)
* Avoid chasing if price is already > MAX_VWAP_DISTANCE_PCT above VWAP
* ATR stop loss attached to the signal
"""

from __future__ import annotations

from app.data.models import PortfolioSnapshot
from app.models.enums import OrderType, StockAction
from app.stocks.models import StockFeatures, StockSignal
from app.stocks.strategies.base import BaseStockStrategy


class StockBreakout(BaseStockStrategy):
    name = "stock_breakout"

    MIN_RANGE_PCT = 0.005
    MIN_VOLUME_MULTIPLE = 1.5
    MAX_VWAP_DISTANCE_PCT = 1.5
    ATR_STOP_MULTIPLIER = 1.5

    def generate_signal(
        self, features: StockFeatures, portfolio: PortfolioSnapshot
    ) -> StockSignal | None:
        if features.last_price <= 0 or features.high_of_day <= 0:
            return None

        day_range = features.high_of_day - features.low_of_day
        if features.low_of_day <= 0 or day_range / features.low_of_day < self.MIN_RANGE_PCT:
            return None

        breaking_high = features.last_price >= features.high_of_day * 0.999
        volume_surge = (
            features.relative_volume >= self.MIN_VOLUME_MULTIPLE
            or features.volume_surge_ratio >= self.MIN_VOLUME_MULTIPLE
        )

        # Avoid chasing if extended from VWAP.
        if (
            features.vwap > 0
            and abs(features.distance_from_vwap_pct) > self.MAX_VWAP_DISTANCE_PCT
        ):
            return None

        if breaking_high and volume_surge:
            stop_price = (
                features.last_price - features.atr_14 * self.ATR_STOP_MULTIPLIER
                if features.atr_14 > 0
                else None
            )
            confidence = min(
                0.85,
                0.5 + max(features.relative_volume, features.volume_surge_ratio) * 0.1,
            )
            return StockSignal(
                strategy_name=self.name,
                symbol=features.symbol,
                action=StockAction.BUY,
                confidence=confidence,
                suggested_price=features.last_price,
                order_type=OrderType.MARKET,
                stop_price=stop_price,
                rationale=(
                    f"High-of-day breakout at {features.last_price:.2f}, "
                    f"vol_surge={features.volume_surge_ratio:.2f}, "
                    f"rel_vol={features.relative_volume:.2f}, "
                    f"ATR stop={stop_price:.2f}" if stop_price else "no ATR stop"
                ),
            )

        return None
