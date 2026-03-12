"""
Domain models shared across the entire trading system.

All models are Pydantic v2 BaseModels.  Enums are str-based for easy
serialization.  Every model is exchange-agnostic — exchange-specific
details live in the ``exchange_data`` dict when needed.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.utils.helpers import utc_now


# ── Enums ──────────────────────────────────────────────────────────────


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OutcomeSide(str, Enum):
    YES = "YES"
    NO = "NO"


TokenSide = OutcomeSide


class SignalAction(str, Enum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    SELL_YES = "SELL_YES"
    SELL_NO = "SELL_NO"
    HOLD = "HOLD"
    CANCEL_ALL = "CANCEL_ALL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class Exchange(str, Enum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


class TradingMode(str, Enum):
    DRY_RUN = "dry_run"
    LIVE = "live"


# ── Market ─────────────────────────────────────────────────────────────


class MarketToken(BaseModel):
    token_id: str
    instrument_id: str = ""
    outcome: str = ""

    def __init__(self, **data: Any) -> None:
        if "instrument_id" not in data or not data["instrument_id"]:
            data["instrument_id"] = data.get("token_id", "")
        super().__init__(**data)


class Market(BaseModel):
    condition_id: str = ""
    market_id: str = ""
    question: str = ""
    slug: str = ""
    tokens: list[MarketToken] = Field(default_factory=list)
    end_date: str | None = None
    active: bool = True
    minimum_order_size: float = 1.0
    minimum_tick_size: float = 0.01
    exchange: str = "polymarket"
    exchange_data: dict[str, Any] = Field(default_factory=dict)
    category: str = ""

    def __init__(self, **data: Any) -> None:
        if "market_id" not in data or not data.get("market_id"):
            data["market_id"] = data.get("condition_id", "")
        if "condition_id" not in data or not data.get("condition_id"):
            data["condition_id"] = data.get("market_id", "")
        super().__init__(**data)


# ── Orderbook ──────────────────────────────────────────────────────────


class PriceLevel(BaseModel):
    price: float = 0.0
    size: float = 0.0


class OrderbookSnapshot(BaseModel):
    market_id: str = ""
    token_id: str = ""
    instrument_id: str = ""
    exchange: str = ""
    bids: list[PriceLevel] = Field(default_factory=list)
    asks: list[PriceLevel] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is not None and ba is not None:
            return ba - bb
        return None

    @property
    def mid_price(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is not None and ba is not None:
            return (bb + ba) / 2.0
        return None


# ── Trade ──────────────────────────────────────────────────────────────


class Trade(BaseModel):
    market_id: str = ""
    token_id: str = ""
    instrument_id: str = ""
    exchange: str = ""
    price: float = 0.0
    size: float = 0.0
    side: Side = Side.BUY
    timestamp: datetime = Field(default_factory=utc_now)


# ── Features ───────────────────────────────────────────────────────────


class MarketFeatures(BaseModel):
    market_id: str = ""
    token_id: str = ""
    instrument_id: str = ""
    exchange: str = ""
    timestamp: datetime = Field(default_factory=utc_now)

    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    mid_price: float | None = None
    microprice: float | None = None
    orderbook_imbalance: float | None = None

    bid_depth_5c: float = 0.0
    ask_depth_5c: float = 0.0

    recent_trade_flow: float = 0.0
    volatility_1m: float = 0.0
    momentum_1m: float = 0.0
    momentum_5m: float = 0.0
    momentum_15m: float = 0.0
    trade_count_1m: int = 0
    last_trade_price: float | None = None

    seconds_since_last_update: float = 0.0

    def __init__(self, **data: Any) -> None:
        if "instrument_id" not in data and "token_id" in data:
            data["instrument_id"] = data["token_id"]
        super().__init__(**data)


# ── Signal ─────────────────────────────────────────────────────────────


class Signal(BaseModel):
    strategy_name: str = ""
    market_id: str = ""
    token_id: str = ""
    instrument_id: str = ""
    exchange: str = ""
    action: SignalAction = SignalAction.HOLD
    confidence: float = 0.0
    suggested_price: float | None = None
    suggested_size: float | None = None
    rationale: str = ""
    timestamp: datetime = Field(default_factory=utc_now)

    def __init__(self, **data: Any) -> None:
        if "instrument_id" not in data and "token_id" in data:
            data["instrument_id"] = data["token_id"]
        elif "token_id" not in data and "instrument_id" in data:
            data["token_id"] = data["instrument_id"]
        super().__init__(**data)


# ── Order ──────────────────────────────────────────────────────────────


class Order(BaseModel):
    order_id: str = ""
    market_id: str = ""
    token_id: str = ""
    instrument_id: str = ""
    exchange: str = ""
    side: Side = Side.BUY
    price: float = 0.0
    size: float = 0.0
    filled_size: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    exchange_order_id: str | None = None
    signal_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
        }


# ── Position ───────────────────────────────────────────────────────────


class Position(BaseModel):
    market_id: str = ""
    token_id: str = ""
    instrument_id: str = ""
    exchange: str = ""
    token_side: OutcomeSide = OutcomeSide.YES
    size: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    last_mark_price: float = 0.0
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def notional(self) -> float:
        return self.size * self.avg_entry_price

    @property
    def market_value(self) -> float:
        if self.last_mark_price > 0:
            return self.size * self.last_mark_price
        return self.notional


# ── Portfolio ──────────────────────────────────────────────────────────


class PortfolioSnapshot(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    cash: float = 0.0
    positions: list[Position] = Field(default_factory=list)
    total_exposure: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_realized_pnl: float = 0.0
    daily_pnl: float = 0.0


# ── Backtest ───────────────────────────────────────────────────────────


class BacktestResult(BaseModel):
    strategy_name: str = ""
    total_snapshots: int = 0
    total_signals: int = 0
    total_trades: int = 0
    total_fills: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    final_cash: float = 0.0
    final_equity: float = 0.0
    parameters: dict[str, Any] = Field(default_factory=dict)
