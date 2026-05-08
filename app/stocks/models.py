"""Stock-specific data models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import OrderType, StockAction
from app.utils.helpers import utc_now


class StockBar(BaseModel):
    """OHLCV bar for a stock."""

    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime
    vwap: float | None = None


class StockFeatures(BaseModel):
    """Computed features for a stock symbol at a point in time.

    Includes the full Phase-1 indicator set required by the Jetson stock
    strategies: EMA-9/21/50, RSI-14, MACD (line/signal/hist), VWAP, ATR-14,
    Bollinger Bands, volume surge ratio, relative volume, multi-timeframe
    momentum, distance from VWAP, volatility score, and trend strength.
    """

    symbol: str
    timestamp: datetime
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0

    # Volume
    volume_1m: int = 0
    volume_5m: int = 0
    volume_today: int = 0
    relative_volume: float = 1.0
    volume_surge_ratio: float = 1.0

    # VWAP / price location
    vwap: float = 0.0
    price_vs_vwap: float = 0.0
    distance_from_vwap_pct: float = 0.0
    high_of_day: float = 0.0
    low_of_day: float = 0.0

    # Trend / EMAs
    sma_20: float = 0.0
    ema_9: float = 0.0
    ema_21: float = 0.0
    ema_50: float = 0.0
    trend_strength: float = 0.0

    # Oscillators
    rsi_14: float = 50.0
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0

    # Volatility
    atr_14: float = 0.0
    volatility_1h: float = 0.0
    volatility_score: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_pct_b: float = 0.5

    # Momentum
    momentum_1m: float = 0.0
    momentum_5m: float = 0.0
    momentum_15m: float = 0.0


class StockSignal(BaseModel):
    """Signal produced by a stock strategy."""

    strategy_name: str
    symbol: str
    action: StockAction
    confidence: float = 0.0
    suggested_price: float | None = None
    suggested_quantity: int | None = None
    order_type: OrderType = OrderType.MARKET
    stop_price: float | None = None
    rationale: str = ""
    timestamp: datetime = None  # type: ignore[assignment]

    def __init__(self, **kwargs):
        if "timestamp" not in kwargs or kwargs["timestamp"] is None:
            kwargs["timestamp"] = utc_now()
        super().__init__(**kwargs)
