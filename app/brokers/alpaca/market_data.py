"""Alpaca market data client using alpaca-py."""

from __future__ import annotations

from typing import Any

from app.brokers.base import BaseBrokerMarketData
from app.config.settings import Settings
from app.monitoring import get_logger

logger = get_logger(__name__)


class AlpacaMarketData(BaseBrokerMarketData):
    """REST market data for Alpaca."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.alpaca_api_key
        self._secret_key = settings.alpaca_secret_key
        self._paper = settings.alpaca_paper
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from alpaca.data import StockHistoricalDataClient
                self._client = StockHistoricalDataClient(
                    api_key=self._api_key,
                    secret_key=self._secret_key,
                )
            except ImportError:
                logger.warning("alpaca-py not installed, market data unavailable")
        return self._client

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        client = self._ensure_client()
        if client is None:
            return {"symbol": symbol, "bid": 0, "ask": 0, "last": 0}
        try:
            from alpaca.data.requests import StockLatestQuoteRequest
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = client.get_stock_latest_quote(req)
            q = quotes.get(symbol)
            if q:
                return {
                    "symbol": symbol,
                    "bid": float(q.bid_price),
                    "ask": float(q.ask_price),
                    "bid_size": int(q.bid_size),
                    "ask_size": int(q.ask_size),
                }
        except Exception as exc:
            logger.error("alpaca_quote_error", symbol=symbol, error=str(exc))
        return {"symbol": symbol, "bid": 0, "ask": 0}

    async def get_bars(
        self, symbol: str, timeframe: str = "1Min", limit: int = 100
    ) -> list[dict[str, Any]]:
        client = self._ensure_client()
        if client is None:
            return []
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            tf = TimeFrame.Minute
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                limit=limit,
            )
            bars = client.get_stock_bars(req)
            result = []
            for bar in bars[symbol]:
                result.append({
                    "symbol": symbol,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": int(bar.volume),
                    "timestamp": bar.timestamp.isoformat() if hasattr(bar.timestamp, "isoformat") else str(bar.timestamp),
                    "vwap": float(bar.vwap) if hasattr(bar, "vwap") and bar.vwap else None,
                })
            return result
        except Exception as exc:
            logger.error("alpaca_bars_error", symbol=symbol, error=str(exc))
            return []

    async def get_snapshot(self, symbol: str) -> dict[str, Any]:
        client = self._ensure_client()
        if client is None:
            return {"symbol": symbol}
        try:
            from alpaca.data.requests import StockSnapshotRequest
            req = StockSnapshotRequest(symbol_or_symbols=symbol)
            snapshots = client.get_stock_snapshot(req)
            snap = snapshots.get(symbol)
            if snap:
                return {
                    "symbol": symbol,
                    "latest_trade_price": float(snap.latest_trade.price) if snap.latest_trade else 0,
                    "latest_quote_bid": float(snap.latest_quote.bid_price) if snap.latest_quote else 0,
                    "latest_quote_ask": float(snap.latest_quote.ask_price) if snap.latest_quote else 0,
                }
        except Exception as exc:
            logger.error("alpaca_snapshot_error", symbol=symbol, error=str(exc))
        return {"symbol": symbol}

    async def get_tradable_assets(self, **filters: Any) -> list[dict[str, Any]]:
        try:
            from alpaca.trading.client import TradingClient
            trading = TradingClient(self._api_key, self._secret_key, paper=self._paper)
            from alpaca.trading.requests import GetAssetsRequest
            from alpaca.trading.enums import AssetClass as AlpacaAssetClass
            req = GetAssetsRequest(asset_class=AlpacaAssetClass.US_EQUITY, status="active")
            assets = trading.get_all_assets(req)
            return [
                {
                    "symbol": a.symbol,
                    "name": a.name,
                    "exchange": a.exchange.value if a.exchange else "",
                    "tradable": a.tradable,
                    "shortable": a.shortable,
                    "fractionable": a.fractionable,
                }
                for a in assets
                if a.tradable
            ]
        except Exception as exc:
            logger.error("alpaca_assets_error", error=str(exc))
            return []

    async def close(self) -> None:
        self._client = None
