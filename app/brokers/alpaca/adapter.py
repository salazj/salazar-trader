"""Alpaca broker adapter — wires together market data, execution, and streaming."""

from __future__ import annotations

from datetime import datetime

from app.brokers.alpaca.execution import AlpacaExecution
from app.brokers.alpaca.market_data import AlpacaMarketData
from app.brokers.alpaca.market_hours import MarketHoursManager
from app.brokers.alpaca.streaming import AlpacaStreaming
from app.brokers.base import (
    BaseBrokerAdapter,
    BaseBrokerExecution,
    BaseBrokerMarketData,
    BaseBrokerStreaming,
)
from app.config.settings import Settings
from app.models.enums import AssetClass


class AlpacaAdapter(BaseBrokerAdapter):
    """Full Alpaca adapter conforming to the broker abstraction."""

    def __init__(self, settings: Settings) -> None:
        self._market_data = AlpacaMarketData(settings)
        self._execution = AlpacaExecution(settings)
        self._streaming = AlpacaStreaming(settings)
        self._hours = MarketHoursManager()

    @property
    def broker_name(self) -> str:
        return "alpaca"

    @property
    def asset_class(self) -> AssetClass:
        return AssetClass.EQUITIES

    @property
    def market_data(self) -> BaseBrokerMarketData:
        return self._market_data

    @property
    def execution(self) -> BaseBrokerExecution:
        return self._execution

    @property
    def streaming(self) -> BaseBrokerStreaming:
        return self._streaming

    def is_market_open(self) -> bool:
        return self._hours.is_market_open()

    def next_market_open(self) -> datetime:
        return self._hours.next_market_open()

    def next_market_close(self) -> datetime:
        return self._hours.next_market_close()

    def minutes_to_close(self) -> float:
        return self._hours.minutes_to_close()

    async def close(self) -> None:
        await self._streaming.disconnect()
        await self._execution.close()
        await self._market_data.close()
