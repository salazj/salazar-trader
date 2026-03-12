"""
Passive Market Maker Strategy

Places small maker quotes on both sides when:
- The spread is wide enough to be profitable after fees
- Liquidity is sufficient (not a dead market)
- The spread is not too wide (indicates low-quality market)
- Orderbook imbalance is not extreme (avoids adverse selection)

Designed for low-capital learning. NOT an aggressive HFT strategy.
Cancels and requotes conservatively to avoid accumulating stale orders.
"""

from __future__ import annotations

import time

from app.config.settings import Settings
from app.data.models import MarketFeatures, PortfolioSnapshot, Signal, SignalAction
from app.strategies.base import BaseStrategy, StrategyRegistry


@StrategyRegistry.register
class PassiveMarketMaker(BaseStrategy):
    name = "passive_market_maker"

    # Minimum spread to justify quoting (must cover fees + edge)
    MIN_EDGE_SPREAD = 0.02
    # Maximum imbalance magnitude before we lean away or skip
    MAX_IMBALANCE = 0.7
    # Minimum depth on each side within 5c to consider market liquid
    MIN_DEPTH = 5.0
    # Offset from best bid/ask to avoid being first in line
    QUOTE_OFFSET = 0.01
    # Only trade in this price range (avoids longshots and near-certainties)
    TRADEABLE_PRICE_LOW = 0.20
    TRADEABLE_PRICE_HIGH = 0.80
    # Minimum 24h volume to avoid illiquid markets
    MIN_VOLUME_24H = 50
    # Cooldown per instrument to prevent order spam
    COOLDOWN_SECONDS = 300.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._last_signal_per_instrument: dict[str, float] = {}

    def generate_signal(
        self,
        features: MarketFeatures,
        portfolio: PortfolioSnapshot,
    ) -> Signal | None:
        if not self._market_suitable(features):
            return None

        assert features.best_bid is not None
        assert features.best_ask is not None
        assert features.spread is not None

        imbalance = features.orderbook_imbalance or 0.0

        # Lean quotes toward the heavier side (more likely to fill on the light side)
        if imbalance > self.MAX_IMBALANCE:
            return self._quote_signal(features, SignalAction.SELL_YES, features.best_ask, imbalance)
        elif imbalance < -self.MAX_IMBALANCE:
            return self._quote_signal(features, SignalAction.BUY_YES, features.best_bid, imbalance)

        # Balanced book: prefer buying Yes at a discount (joining the bid)
        action = SignalAction.BUY_YES
        price = features.best_bid + self.QUOTE_OFFSET
        # Ensure we don't cross the spread
        if price >= features.best_ask:
            price = features.best_bid

        confidence = self._compute_confidence(features)
        inst_id = features.instrument_id or features.token_id
        self._last_signal_per_instrument[inst_id] = time.time()

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
                f"spread={features.spread:.4f} imbal={imbalance:.2f} "
                f"bid_depth={features.bid_depth_5c:.1f} ask_depth={features.ask_depth_5c:.1f}"
            ),
        )

    def _market_suitable(self, f: MarketFeatures) -> bool:
        if f.best_bid is None or f.best_ask is None or f.spread is None:
            return False
        mid = (f.best_bid + f.best_ask) / 2.0
        if mid < self.TRADEABLE_PRICE_LOW or mid > self.TRADEABLE_PRICE_HIGH:
            return False
        volume = getattr(f, "volume_24h", 0) or 0
        if volume < self.MIN_VOLUME_24H:
            return False
        if f.spread < self.MIN_EDGE_SPREAD:
            return False
        if f.spread > self.settings.max_spread_threshold:
            return False
        if f.bid_depth_5c < self.MIN_DEPTH or f.ask_depth_5c < self.MIN_DEPTH:
            return False
        if f.seconds_since_last_update > 30:
            return False
        inst_id = f.instrument_id or f.token_id
        now = time.time()
        last = self._last_signal_per_instrument.get(inst_id, 0.0)
        if (now - last) < self.COOLDOWN_SECONDS:
            return False
        return True

    def _quote_signal(
        self,
        features: MarketFeatures,
        action: SignalAction,
        ref_price: float,
        imbalance: float,
    ) -> Signal:
        inst_id = features.instrument_id or features.token_id
        self._last_signal_per_instrument[inst_id] = time.time()
        return Signal(
            strategy_name=self.name,
            market_id=features.market_id,
            token_id=features.instrument_id or features.token_id,
            instrument_id=features.instrument_id or features.token_id,
            exchange=features.exchange,
            action=action,
            confidence=0.3,
            suggested_price=ref_price,
            suggested_size=self.settings.default_order_size,
            rationale=f"leaning_due_to_imbalance={imbalance:.2f}",
        )

    def _compute_confidence(self, f: MarketFeatures) -> float:
        """Higher confidence when spread is wider and book is balanced."""
        assert f.spread is not None
        spread_score = min(f.spread / 0.10, 1.0)
        depth_score = min((f.bid_depth_5c + f.ask_depth_5c) / 100.0, 1.0)
        imbal_penalty = abs(f.orderbook_imbalance or 0) * 0.3
        return max(0.1, min(1.0, (spread_score * 0.5 + depth_score * 0.3) - imbal_penalty))
