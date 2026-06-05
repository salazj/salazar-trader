"""
Abstract base classes for stock broker adapters.

Every stock broker (Alpaca, Interactive Brokers, etc.) must implement these
interfaces so the equities trading path can remain broker-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Coroutine

from app.models.enums import AssetClass, OrderType, TimeInForce

MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class BaseBrokerMarketData(ABC):
    """REST market data for stock brokers."""

    @abstractmethod
    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """Fetch latest quote for a symbol."""

    @abstractmethod
    async def get_bars(
        self, symbol: str, timeframe: str = "1Min", limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch historical bars."""

    @abstractmethod
    async def get_snapshot(self, symbol: str) -> dict[str, Any]:
        """Fetch full snapshot (quote + last trade + bar)."""

    @abstractmethod
    async def get_tradable_assets(self, **filters: Any) -> list[dict[str, Any]]:
        """List tradable assets with optional filters."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up HTTP resources."""


class BaseBrokerExecution(ABC):
    """Order execution for stock brokers."""

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
        stop_price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> dict[str, Any]:
        """Submit an order. Returns order details with broker-assigned ID."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel a single order by broker-assigned ID."""

    @abstractmethod
    async def cancel_all(self) -> None:
        """Cancel all open orders."""

    @abstractmethod
    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Get order details by ID."""

    @abstractmethod
    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Fetch all open orders."""

    @abstractmethod
    async def get_account(self) -> dict[str, Any]:
        """Fetch account info (balance, buying power, etc.)."""

    @abstractmethod
    async def get_positions(self) -> list[dict[str, Any]]:
        """Fetch all open positions."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up HTTP resources."""

    @property
    @abstractmethod
    def is_dry_run(self) -> bool:
        """Whether this client is in simulation mode."""

    async def place_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        *,
        stop_price: float | None = None,
        take_profit_price: float | None = None,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> dict[str, Any]:
        """Submit an entry with attached protective exit legs.

        Default implementation falls back to a plain entry order (no broker-side
        protection) for brokers that don't support brackets. Alpaca overrides
        this with a true bracket/OTO order.
        """
        return await self.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
            stop_price=stop_price,
            time_in_force=time_in_force,
        )

    async def close_position(self, symbol: str) -> dict[str, Any]:
        """Flatten an open position for ``symbol``. Default: no-op."""
        return {"symbol": symbol, "status": "unsupported"}

    async def cancel_orders_for_symbol(self, symbol: str) -> int:
        """Cancel all resting orders for ``symbol``. Returns count cancelled."""
        count = 0
        try:
            for o in await self.get_open_orders():
                if str(o.get("symbol", "")).upper() == symbol.upper():
                    try:
                        await self.cancel_order(str(o.get("id", "")))
                        count += 1
                    except Exception:
                        continue
        except Exception:
            pass
        return count


class BaseBrokerStreaming(ABC):
    """Real-time data feed via WebSocket for stock brokers."""

    @abstractmethod
    async def subscribe_quotes(self, symbols: list[str]) -> None:
        """Subscribe to real-time quotes."""

    @abstractmethod
    async def subscribe_bars(self, symbols: list[str]) -> None:
        """Subscribe to real-time minute bars."""

    @abstractmethod
    async def subscribe_trades(self, symbols: list[str]) -> None:
        """Subscribe to real-time trades."""

    @abstractmethod
    async def subscribe_order_updates(self) -> None:
        """Subscribe to order/fill updates."""

    @abstractmethod
    def on(self, event_type: str, handler: MessageHandler) -> None:
        """Register a handler for a specific event type."""

    @abstractmethod
    async def connect(self) -> None:
        """Start the streaming connection with reconnection."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully disconnect."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the stream is currently connected."""


class BaseBrokerAdapter(ABC):
    """
    Container that wires together market data, execution, and streaming
    clients for a specific stock broker.
    """

    @property
    @abstractmethod
    def broker_name(self) -> str:
        """Name of the broker (e.g. 'alpaca')."""

    @property
    @abstractmethod
    def asset_class(self) -> AssetClass:
        """Always AssetClass.EQUITIES for broker adapters."""

    @property
    @abstractmethod
    def market_data(self) -> BaseBrokerMarketData:
        """REST market data client."""

    @property
    @abstractmethod
    def execution(self) -> BaseBrokerExecution:
        """Order execution client."""

    @property
    @abstractmethod
    def streaming(self) -> BaseBrokerStreaming:
        """Real-time data client."""

    @abstractmethod
    def is_market_open(self) -> bool:
        """Whether the market is currently open for trading."""

    @abstractmethod
    def next_market_open(self) -> datetime:
        """Next market open time (UTC)."""

    @abstractmethod
    def next_market_close(self) -> datetime:
        """Next market close time (UTC)."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up all resources."""
