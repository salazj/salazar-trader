"""
Tests for exchange base interfaces and adapter contracts.

Verifies that the Polymarket adapter implements all required
abstract methods from the base classes.
"""

from __future__ import annotations

import inspect

import pytest

from app.exchanges.base import (
    BaseExchangeAdapter,
    BaseExecutionClient,
    BaseMarketDataClient,
    BaseWebSocketClient,
    Exchange,
)


class TestExchangeEnum:
    def test_polymarket_value(self):
        assert Exchange.POLYMARKET.value == "polymarket"

    def test_string_comparison(self):
        assert Exchange.POLYMARKET == "polymarket"


class TestBaseInterfaces:
    """Verify that the base ABCs define the expected methods."""

    def test_market_data_client_methods(self):
        expected = {"get_markets", "get_all_markets", "get_market", "get_orderbook", "get_midpoint", "close"}
        abstract_methods = set(BaseMarketDataClient.__abstractmethods__)
        assert abstract_methods == expected

    def test_execution_client_methods(self):
        expected = {"place_order", "cancel_order", "cancel_all", "get_balance", "get_open_positions", "is_dry_run"}
        abstract_methods = set(BaseExecutionClient.__abstractmethods__)
        assert abstract_methods == expected

    def test_websocket_client_methods(self):
        expected = {
            "subscribe_book", "subscribe_trades", "subscribe_user",
            "on", "connect", "disconnect",
            "is_connected", "seconds_since_last_message", "is_stale",
        }
        abstract_methods = set(BaseWebSocketClient.__abstractmethods__)
        assert abstract_methods == expected

    def test_exchange_adapter_methods(self):
        expected = {"exchange", "market_data", "execution", "websocket", "close"}
        abstract_methods = set(BaseExchangeAdapter.__abstractmethods__)
        assert abstract_methods == expected


class TestPolymarketImplementation:
    """Verify Polymarket adapter fully implements the base interfaces."""

    def test_adapter_implements_base(self):
        from app.exchanges.polymarket.adapter import PolymarketAdapter
        assert issubclass(PolymarketAdapter, BaseExchangeAdapter)

    def test_market_data_implements_base(self):
        from app.exchanges.polymarket.market_data import PolymarketMarketDataClient
        assert issubclass(PolymarketMarketDataClient, BaseMarketDataClient)

    def test_execution_implements_base(self):
        from app.exchanges.polymarket.execution import PolymarketExecutionClient
        assert issubclass(PolymarketExecutionClient, BaseExecutionClient)

    def test_websocket_implements_base(self):
        from app.exchanges.polymarket.websocket import PolymarketWebSocketClient
        assert issubclass(PolymarketWebSocketClient, BaseWebSocketClient)

    def test_adapter_instantiation(self, settings):
        from app.exchanges.polymarket.adapter import PolymarketAdapter
        adapter = PolymarketAdapter(settings)
        assert adapter.exchange == Exchange.POLYMARKET
        assert adapter.market_data is not None
        assert adapter.execution is not None
        assert adapter.websocket is not None
