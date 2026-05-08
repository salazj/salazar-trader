"""Momentum strategy for stocks.

Conditions for a BUY:
* EMA-9 > EMA-21 (short-term up) and price above VWAP
* RSI-14 below the overbought threshold
* 5-minute momentum positive and above ``MOMENTUM_THRESHOLD``
* Volume confirmation: ``volume_surge_ratio`` >= ``MIN_VOLUME_SURGE``
* ATR-based stop loss attached to the signal
"""

from __future__ import annotations

from app.data.models import PortfolioSnapshot
from app.models.enums import OrderType, StockAction
from app.stocks.models import StockFeatures, StockSignal
from app.stocks.strategies.base import BaseStockStrategy


class StockMomentum(BaseStockStrategy):
    name = "stock_momentum"

    MOMENTUM_THRESHOLD = 0.003
    RSI_OVERBOUGHT = 70.0
    RSI_OVERSOLD = 30.0
    MIN_VOLUME_SURGE = 1.2
    ATR_STOP_MULTIPLIER = 1.5
    COOLDOWN_BARS = 5

    def __init__(self) -> None:
        self._last_signal_bar: dict[str, int] = {}
        self._bar_count: int = 0

    def generate_signal(
        self, features: StockFeatures, portfolio: PortfolioSnapshot
    ) -> StockSignal | None:
        self._bar_count += 1
        symbol = features.symbol

        last_bar = self._last_signal_bar.get(symbol, 0)
        if self._bar_count - last_bar < self.COOLDOWN_BARS:
            return None

        if features.last_price <= 0 or features.ema_9 <= 0:
            return None

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

        if price_above_ema and price_above_vwap and momentum_strong and rsi_ok and volume_ok:
            confidence = min(0.9, 0.5 + features.momentum_5m * 10)
            self._last_signal_bar[symbol] = self._bar_count
            stop = (
                features.last_price - features.atr_14 * self.ATR_STOP_MULTIPLIER
                if features.atr_14 > 0
                else None
            )
            return StockSignal(
                strategy_name=self.name,
                symbol=symbol,
                action=StockAction.BUY,
                confidence=confidence,
                suggested_price=features.last_price,
                order_type=OrderType.LIMIT,
                stop_price=stop,
                rationale=(
                    f"EMA9>VWAP, mom_5m={features.momentum_5m:.4f}, "
                    f"RSI={features.rsi_14:.1f}, "
                    f"vol_surge={features.volume_surge_ratio:.2f}"
                ),
            )

        momentum_reversed = features.momentum_5m < -self.MOMENTUM_THRESHOLD
        price_below_ema = features.last_price < features.ema_9

        if price_below_ema and momentum_reversed:
            confidence = min(0.8, 0.4 + abs(features.momentum_5m) * 10)
            self._last_signal_bar[symbol] = self._bar_count
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
