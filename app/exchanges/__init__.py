"""Exchange adapter layer — pluggable exchange implementations."""

from app.exchanges.base import (
    BaseExchangeAdapter,
    BaseExecutionClient,
    BaseMarketDataClient,
    BaseWebSocketClient,
    Exchange,
)


def build_exchange_adapter(settings) -> BaseExchangeAdapter:
    """Factory: instantiate the correct adapter based on settings.exchange."""
    from app.exchanges.polymarket.adapter import PolymarketAdapter

    exchange = settings.exchange.lower()
    if exchange == "polymarket":
        return PolymarketAdapter(settings)
    raise ValueError(f"Unknown exchange: {exchange!r}. Expected 'polymarket'.")


__all__ = [
    "BaseExchangeAdapter",
    "BaseExecutionClient",
    "BaseMarketDataClient",
    "BaseWebSocketClient",
    "Exchange",
    "build_exchange_adapter",
]
