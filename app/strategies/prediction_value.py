"""
Prediction Market Value Strategy

Identifies potential value in prediction markets by detecting:
1. Extreme prices (near 0 or 1) that imply near-certainty — often mispriced
2. Mean-reversion signals when price moves sharply away from recent mid
3. Spread-capture opportunities when bid-ask is wide enough

Unlike the market-maker/scalper strategies that need deep orderbooks,
this works with the thin books typical of Kalshi prediction markets.
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

EDGE_THRESHOLD = 0.01
EXTREME_LOW = 0.15
EXTREME_HIGH = 0.85
MIN_SPREAD = 0.005
MAX_SPREAD = 0.30
MOMENTUM_THRESHOLD = 0.02


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
            logger.info(
                "prediction_value_skip_no_mid",
                market_id=features.market_id,
                instrument_id=features.instrument_id,
                mid=mid,
                bid=features.best_bid,
                ask=features.best_ask,
            )
            return None

        bid = features.best_bid
        ask = features.best_ask
        spread = features.spread

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
            extreme_low=mid < EXTREME_LOW,
            extreme_high=mid > EXTREME_HIGH,
        )

        if spread is not None and (spread < MIN_SPREAD or spread > MAX_SPREAD):
            return None

        signal = self._check_extreme_value(mid, bid, ask, spread, features)
        if signal:
            return signal

        signal = self._check_mean_reversion(mid, history, features)
        if signal:
            return signal

        signal = self._check_momentum_edge(mid, features)
        if signal:
            return signal

        return None

    def _check_extreme_value(
        self,
        mid: float,
        bid: float | None,
        ask: float | None,
        spread: float | None,
        features: MarketFeatures,
    ) -> Signal | None:
        """Detect mispriced extremes: markets priced very low or very high
        often have edge because small probability events are underpriced."""
        if mid < EXTREME_LOW and bid is not None and bid > 0.01:
            edge = EXTREME_LOW - mid
            if edge >= EDGE_THRESHOLD:
                return self._make_signal(
                    features,
                    SignalAction.BUY_YES,
                    confidence=min(0.45 + edge, 0.70),
                    price=bid + 0.01,
                    rationale=f"extreme_low_value: price={mid:.3f} edge={edge:.3f}",
                )

        if mid > EXTREME_HIGH and ask is not None and ask < 0.99:
            edge = mid - EXTREME_HIGH
            if edge >= EDGE_THRESHOLD:
                return self._make_signal(
                    features,
                    SignalAction.BUY_NO,
                    confidence=min(0.45 + edge, 0.70),
                    price=1.0 - ask + 0.01,
                    rationale=f"extreme_high_value: price={mid:.3f} edge={edge:.3f}",
                )

        return None

    def _check_mean_reversion(
        self,
        mid: float,
        history: list[float],
        features: MarketFeatures,
    ) -> Signal | None:
        """If price moved sharply from recent average, bet on reversion."""
        if len(history) < 10:
            return None

        avg = sum(history[-20:]) / len(history[-20:])
        deviation = mid - avg

        if abs(deviation) < MOMENTUM_THRESHOLD:
            return None

        if deviation > MOMENTUM_THRESHOLD:
            return self._make_signal(
                features,
                SignalAction.SELL_YES,
                confidence=min(0.40 + abs(deviation), 0.65),
                rationale=f"mean_reversion_sell: mid={mid:.3f} avg={avg:.3f} dev={deviation:+.3f}",
            )
        else:
            return self._make_signal(
                features,
                SignalAction.BUY_YES,
                confidence=min(0.40 + abs(deviation), 0.65),
                rationale=f"mean_reversion_buy: mid={mid:.3f} avg={avg:.3f} dev={deviation:+.3f}",
            )

    def _check_momentum_edge(
        self,
        mid: float,
        features: MarketFeatures,
    ) -> Signal | None:
        """Use short-term momentum and orderbook imbalance as edge signal."""
        momentum = features.momentum_1m
        imbalance = features.orderbook_imbalance

        if momentum is None or imbalance is None:
            return None
        if abs(momentum) < 0.02:
            return None

        if momentum > 0.02 and imbalance > 0.2:
            return self._make_signal(
                features,
                SignalAction.BUY_YES,
                confidence=min(0.40 + abs(momentum) + abs(imbalance) * 0.3, 0.65),
                rationale=f"momentum_edge_buy: mom={momentum:+.3f} imb={imbalance:+.3f}",
            )
        elif momentum < -0.02 and imbalance < -0.2:
            return self._make_signal(
                features,
                SignalAction.SELL_YES,
                confidence=min(0.40 + abs(momentum) + abs(imbalance) * 0.3, 0.65),
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
