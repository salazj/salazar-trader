"""
Feature computation from orderbook snapshots and trade history.

All pure functions are stateless and tested independently.
FeatureEngine maintains a rolling trade buffer per instrument.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Sequence

from app.data.models import MarketFeatures, OrderbookSnapshot, PriceLevel, Trade
from app.utils.helpers import utc_now


def compute_microprice(book: OrderbookSnapshot) -> float | None:
    if not book.bids or not book.asks:
        return None
    best_bid = book.bids[0]
    best_ask = book.asks[0]
    total = best_bid.size + best_ask.size
    if total == 0:
        return None
    return (best_bid.price * best_ask.size + best_ask.price * best_bid.size) / total


def compute_orderbook_imbalance(book: OrderbookSnapshot) -> float | None:
    if not book.bids or not book.asks:
        return None
    bid_vol = sum(l.size for l in book.bids)
    ask_vol = sum(l.size for l in book.asks)
    total = bid_vol + ask_vol
    if total == 0:
        return None
    return (bid_vol - ask_vol) / total


def compute_depth_within(
    levels: Sequence[PriceLevel], reference: float, cents: float
) -> float:
    return sum(l.size for l in levels if abs(l.price - reference) <= cents + 1e-9)


def compute_volatility(prices: Sequence[float]) -> float:
    if len(prices) < 2:
        return 0.0
    returns = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance)


def compute_momentum(prices: Sequence[float]) -> float:
    if len(prices) < 2:
        return 0.0
    return prices[-1] - prices[0]


def compute_trade_flow(trades: Sequence[Trade], window_seconds: float = 60.0) -> float:
    if not trades:
        return 0.0
    now = utc_now()
    flow = 0.0
    for t in trades:
        age = (now - t.timestamp).total_seconds()
        if age <= window_seconds:
            from app.data.models import Side
            sign = 1.0 if t.side == Side.BUY else -1.0
            flow += sign * t.size
    return flow


class FeatureEngine:
    """Stateful feature engine per instrument. Maintains rolling trade buffer."""

    def __init__(
        self,
        market_id: str,
        instrument_id: str = "",
        exchange: str = "",
        max_trades: int = 500,
    ) -> None:
        self._market_id = market_id
        self._instrument_id = instrument_id or market_id
        self._exchange = exchange
        self._trades: deque[Trade] = deque(maxlen=max_trades)
        self._price_history: deque[float] = deque(maxlen=200)
        self._last_update = time.time()
        self.volume_24h: float = 0.0
        self.open_interest: float = 0.0

    def add_trade(self, trade: Trade) -> None:
        self._trades.append(trade)
        self._price_history.append(trade.price)
        self._last_update = time.time()

    def compute(self, book: OrderbookSnapshot | None = None) -> MarketFeatures:
        best_bid = None
        best_ask = None
        microprice = None
        imbalance = None
        bid_depth = 0.0
        ask_depth = 0.0

        if book is not None:
            best_bid = book.bids[0].price if book.bids else None
            best_ask = book.asks[0].price if book.asks else None
            microprice = compute_microprice(book)
            imbalance = compute_orderbook_imbalance(book)
            ref = (best_bid + best_ask) / 2.0 if best_bid and best_ask else 0.5
            bid_depth = compute_depth_within(book.bids, ref, 0.05)
            ask_depth = compute_depth_within(book.asks, ref, 0.05)
            if best_bid is not None or best_ask is not None:
                self._last_update = time.time()

        spread = None
        mid = None
        if best_bid is not None and best_ask is not None:
            spread = best_ask - best_bid
            mid = (best_bid + best_ask) / 2.0

        prices = list(self._price_history)
        trades = list(self._trades)

        now = time.time()
        seconds_since = now - self._last_update

        recent_trades = [
            t for t in trades
            if (utc_now() - t.timestamp).total_seconds() <= 60
        ]

        last_trade_price = trades[-1].price if trades else None

        return MarketFeatures(
            market_id=self._market_id,
            token_id=self._instrument_id,
            instrument_id=self._instrument_id,
            exchange=self._exchange,
            timestamp=utc_now(),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            mid_price=mid,
            microprice=microprice,
            orderbook_imbalance=imbalance,
            bid_depth_5c=bid_depth,
            ask_depth_5c=ask_depth,
            recent_trade_flow=compute_trade_flow(trades),
            volatility_1m=compute_volatility(prices[-60:]) if len(prices) >= 2 else 0.0,
            momentum_1m=compute_momentum(prices[-60:]) if len(prices) >= 2 else 0.0,
            momentum_5m=compute_momentum(prices[-300:]) if len(prices) >= 2 else 0.0,
            momentum_15m=compute_momentum(prices) if len(prices) >= 2 else 0.0,
            trade_count_1m=len(recent_trades),
            last_trade_price=last_trade_price,
            volume_24h=self.volume_24h,
            open_interest=self.open_interest,
            seconds_since_last_update=seconds_since,
        )
