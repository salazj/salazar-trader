"""
Momentum Scalper Strategy

Trades only when short-horizon momentum aligns with:
- Sufficient liquidity
- Acceptable spread
- Positive trade flow confirmation
- Cooldown period between trades to prevent overtrading

Uses 1-minute momentum and trade flow as primary signals.
"""

from __future__ import annotations

import time

from app.config.settings import Settings
from app.data.models import MarketFeatures, PortfolioSnapshot, Signal, SignalAction
from app.strategies.base import BaseStrategy, StrategyRegistry


@StrategyRegistry.register
class MomentumScalper(BaseStrategy):
    name = "momentum_scalper"

    # Minimum absolute momentum to trigger a trade
    MOMENTUM_THRESHOLD = 0.005
    # Minimum net trade flow to confirm direction
    FLOW_THRESHOLD = 2.0
    # Minimum seconds between signals (cooldown)
    COOLDOWN_SECONDS = 120.0
    # Minimum required spread for entry
    MIN_SPREAD = 0.01
    # Minimum liquidity depth
    MIN_DEPTH = 10.0
    # Maximum data staleness
    MAX_STALE_SECONDS = 15.0
    # Only trade in this price range
    TRADEABLE_PRICE_LOW = 0.20
    TRADEABLE_PRICE_HIGH = 0.80
    MIN_VOLUME_24H = 50

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._last_signal_time: float = 0.0

    def generate_signal(
        self,
        features: MarketFeatures,
        portfolio: PortfolioSnapshot,
    ) -> Signal | None:
        if not self._preconditions_met(features):
            return None

        momentum = features.momentum_1m
        flow = features.recent_trade_flow

        # Require momentum and flow to agree in direction
        if momentum > self.MOMENTUM_THRESHOLD and flow > self.FLOW_THRESHOLD:
            action = SignalAction.BUY_YES
            price = features.best_bid  # join the bid, don't cross
        elif momentum < -self.MOMENTUM_THRESHOLD and flow < -self.FLOW_THRESHOLD:
            action = SignalAction.SELL_YES
            price = features.best_ask  # join the ask
        else:
            return None

        confidence = self._compute_confidence(momentum, flow, features)
        self._last_signal_time = time.time()

        return Signal(
            strategy_name=self.name,
            market_id=features.market_id,
            token_id=features.instrument_id or features.token_id,
            instrument_id=features.instrument_id or features.token_id,
            exchange=features.exchange,
            action=action,
            confidence=confidence,
            suggested_price=price,
            suggested_size=self.settings.default_order_size,
            rationale=(
                f"momentum_1m={momentum:.4f} flow={flow:.1f} "
                f"spread={features.spread:.4f} vol_1m={features.volatility_1m:.4f}"
            ),
        )

    def _preconditions_met(self, f: MarketFeatures) -> bool:
        if f.best_bid is None or f.best_ask is None or f.spread is None:
            return False
        mid = (f.best_bid + f.best_ask) / 2.0
        if mid < self.TRADEABLE_PRICE_LOW or mid > self.TRADEABLE_PRICE_HIGH:
            return False
        volume = getattr(f, "volume_24h", 0) or 0
        if volume < self.MIN_VOLUME_24H:
            return False
        if f.spread < self.MIN_SPREAD:
            return False
        if f.spread > self.settings.max_spread_threshold:
            return False
        if f.bid_depth_5c < self.MIN_DEPTH or f.ask_depth_5c < self.MIN_DEPTH:
            return False
        if f.seconds_since_last_update > self.MAX_STALE_SECONDS:
            return False
        if (time.time() - self._last_signal_time) < self.COOLDOWN_SECONDS:
            return False
        return True

    def _compute_confidence(
        self, momentum: float, flow: float, f: MarketFeatures
    ) -> float:
        mom_score = min(abs(momentum) / 0.02, 1.0)
        flow_score = min(abs(flow) / 10.0, 1.0)
        vol_penalty = min(f.volatility_1m / 0.05, 0.5)
        return max(0.1, min(1.0, (mom_score * 0.4 + flow_score * 0.4) - vol_penalty))
