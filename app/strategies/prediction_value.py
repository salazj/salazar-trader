"""
Prediction Market Value Strategy (v2)

Identifies genuine value in prediction markets via:
1. Mean-reversion when price sharply diverges from its recent average
2. Momentum + orderbook imbalance alignment (directional conviction)

DOES NOT buy contracts simply because they are cheap — a 5-cent contract
is almost always correctly priced for a ~5 % event.  True edge requires
either observable price dislocation or news-driven mismatch confirmed
by the NLP layer.
"""

from __future__ import annotations

from app.config.settings import Settings
from app.data.models import (
    MarketFeatures,
    PortfolioSnapshot,
    Signal,
    SignalAction,
)
from app.monitoring import get_logger
from app.strategies.base import BaseStrategy, StrategyRegistry
from app.utils.helpers import utc_now

logger = get_logger(__name__)

MIN_SPREAD = 0.005
MAX_SPREAD = 0.25
MEAN_REVERSION_THRESHOLD = 0.04
MOMENTUM_THRESHOLD = 0.03
MIN_HISTORY = 15
TRADEABLE_PRICE_LOW = 0.20
TRADEABLE_PRICE_HIGH = 0.80
MIN_VOLUME_24H = 50


@StrategyRegistry.register
class PredictionValueStrategy(BaseStrategy):
    """Rule-based L1 strategy for prediction market value detection."""

    name = "prediction_value"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._price_history: dict[str, list[float]] = {}
        self._max_history = 60

    def generate_signal(
        self,
        features: MarketFeatures,
        portfolio: PortfolioSnapshot,
    ) -> Signal | None:
        mid = features.mid_price
        if mid is None or mid <= 0:
            return None

        if mid < TRADEABLE_PRICE_LOW or mid > TRADEABLE_PRICE_HIGH:
            logger.debug(
                "prediction_value_skip_extreme",
                market_id=features.market_id,
                mid=mid,
                reason="outside tradeable range 0.20-0.80",
            )
            return None

        vol_24h = getattr(features, "volume_24h", None) or 0
        if vol_24h < MIN_VOLUME_24H:
            logger.debug(
                "prediction_value_skip_low_volume",
                market_id=features.market_id,
                volume_24h=vol_24h,
            )
            return None

        bid = features.best_bid
        ask = features.best_ask
        spread = features.spread

        if spread is not None and (spread < MIN_SPREAD or spread > MAX_SPREAD):
            return None

        iid = features.instrument_id or features.token_id or features.market_id
        history = self._price_history.setdefault(iid, [])
        history.append(mid)
        if len(history) > self._max_history:
            history.pop(0)

        logger.info(
            "prediction_value_eval",
            market_id=features.market_id,
            instrument_id=iid,
            mid=mid,
            bid=bid,
            ask=ask,
            spread=spread,
            volume_24h=vol_24h,
            history_len=len(history),
        )

        signal = self._check_mean_reversion(mid, history, features)
        if signal:
            return signal

        signal = self._check_momentum_edge(mid, features)
        if signal:
            return signal

        return None

    def _check_mean_reversion(
        self,
        mid: float,
        history: list[float],
        features: MarketFeatures,
    ) -> Signal | None:
        """Bet on reversion when price sharply diverges from recent average."""
        if len(history) < MIN_HISTORY:
            return None

        window = history[-20:]
        avg = sum(window) / len(window)
        deviation = mid - avg

        if abs(deviation) < MEAN_REVERSION_THRESHOLD:
            return None

        if deviation > MEAN_REVERSION_THRESHOLD:
            return self._make_signal(
                features,
                SignalAction.SELL_YES,
                confidence=min(0.35 + abs(deviation) * 2, 0.65),
                rationale=f"mean_reversion_sell: mid={mid:.3f} avg={avg:.3f} dev={deviation:+.3f}",
            )
        else:
            return self._make_signal(
                features,
                SignalAction.BUY_YES,
                confidence=min(0.35 + abs(deviation) * 2, 0.65),
                rationale=f"mean_reversion_buy: mid={mid:.3f} avg={avg:.3f} dev={deviation:+.3f}",
            )

    def _check_momentum_edge(
        self,
        mid: float,
        features: MarketFeatures,
    ) -> Signal | None:
        """Use short-term momentum + orderbook imbalance when aligned."""
        momentum = features.momentum_1m
        imbalance = features.orderbook_imbalance

        if momentum is None or imbalance is None:
            return None
        if abs(momentum) < MOMENTUM_THRESHOLD:
            return None

        if momentum > MOMENTUM_THRESHOLD and imbalance > 0.3:
            return self._make_signal(
                features,
                SignalAction.BUY_YES,
                confidence=min(0.35 + abs(momentum) * 2 + abs(imbalance) * 0.2, 0.65),
                rationale=f"momentum_edge_buy: mom={momentum:+.3f} imb={imbalance:+.3f}",
            )
        elif momentum < -MOMENTUM_THRESHOLD and imbalance < -0.3:
            return self._make_signal(
                features,
                SignalAction.SELL_YES,
                confidence=min(0.35 + abs(momentum) * 2 + abs(imbalance) * 0.2, 0.65),
                rationale=f"momentum_edge_sell: mom={momentum:+.3f} imb={imbalance:+.3f}",
            )

        return None

    def _make_signal(
        self,
        features: MarketFeatures,
        action: SignalAction,
        confidence: float,
        price: float | None = None,
        rationale: str = "",
    ) -> Signal:
        size = self.settings.max_position_per_market * confidence
        size = max(1.0, min(size, self.settings.max_position_per_market))

        logger.info(
            "prediction_value_signal",
            market_id=features.market_id,
            action=action.value,
            confidence=round(confidence, 3),
            rationale=rationale,
        )

        return Signal(
            strategy_name=self.name,
            market_id=features.market_id,
            token_id=features.instrument_id or features.token_id,
            instrument_id=features.instrument_id or features.token_id,
            exchange=features.exchange,
            action=action,
            confidence=confidence,
            suggested_price=price,
            suggested_size=size,
            rationale=rationale,
        )
