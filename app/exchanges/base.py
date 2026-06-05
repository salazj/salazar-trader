"""
Abstract base classes for exchange adapters.

Every exchange (Polymarket, etc.) must implement these interfaces
so the rest of the system can remain exchange-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Coroutine

from app.data.models import Market, Order, OrderbookSnapshot, Position


class Exchange(str, Enum):
    POLYMARKET = "polymarket"


MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class BaseMarketDataClient(ABC):
    """Fetches market metadata and orderbook snapshots via REST."""

    @abstractmethod
    async def get_markets(self, cursor: str = "") -> tuple[list[Market], str]:
        """Fetch a page of markets. Returns (markets, next_cursor)."""

    @abstractmethod
    async def get_all_markets(self, max_pages: int = 50) -> list[Market]:
        """Page through all available markets."""

    @abstractmethod
    async def get_market(self, market_id: str) -> Market | None:
        """Fetch a single market by its exchange-native identifier."""

    @abstractmethod
    async def get_orderbook(self, instrument_id: str) -> dict[str, Any]:
        """Fetch raw orderbook snapshot for an instrument."""

    @abstractmethod
    async def get_midpoint(self, instrument_id: str) -> float | None:
        """Fetch midpoint price for an instrument."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up HTTP resources."""


class BaseExecutionClient(ABC):
    """Places, cancels, and queries orders on an exchange."""

    @abstractmethod
    async def place_order(self, order: Order) -> Order:
        """Submit an order. Returns updated order with status/exchange_order_id."""

    @abstractmethod
    async def cancel_order(self, order: Order) -> Order:
        """Cancel a single order."""

    @abstractmethod
    async def cancel_all(self) -> None:
        """Cancel all open orders."""

    @abstractmethod
    async def get_balance(self) -> float:
        """Get account balance in USD."""

    @abstractmethod
    async def get_open_positions(self) -> list[dict[str, Any]]:
        """Fetch open positions from the exchange."""

    @property
    @abstractmethod
    def is_dry_run(self) -> bool:
        """Whether this client is in simulation mode."""


class BaseWebSocketClient(ABC):
    """Real-time data feed via WebSocket."""

    @abstractmethod
    def subscribe_book(self, instrument_ids: list[str]) -> None:
        """Queue orderbook subscription."""

    @abstractmethod
    def subscribe_trades(self, instrument_ids: list[str]) -> None:
        """Queue trade subscription."""

    @abstractmethod
    def subscribe_user(self) -> None:
        """Subscribe to user-specific order/fill updates."""

    @abstractmethod
    def on(self, event_type: str, handler: MessageHandler) -> None:
        """Register a handler for a specific event type."""

    @abstractmethod
    async def connect(self) -> None:
        """Start the WebSocket connection loop with reconnection."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully disconnect."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the WebSocket is currently connected."""

    @property
    @abstractmethod
    def seconds_since_last_message(self) -> float:
        """Seconds since the last message was received."""

    @property
    @abstractmethod
    def is_stale(self) -> bool:
        """Whether the data feed is considered stale."""


class BaseExchangeAdapter(ABC):
    """
    Container that wires together market data, execution, and websocket
    clients for a specific exchange.
    """

    @property
    @abstractmethod
    def exchange(self) -> Exchange:
        """Which exchange this adapter connects to."""

    @property
    @abstractmethod
    def market_data(self) -> BaseMarketDataClient:
        """REST market data client."""

    @property
    @abstractmethod
    def execution(self) -> BaseExecutionClient:
        """Order execution client."""

    @property
    @abstractmethod
    def websocket(self) -> BaseWebSocketClient:
        """Real-time data client."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up all resources."""
