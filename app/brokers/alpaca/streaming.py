"""Alpaca streaming client for real-time market data.

Uses alpaca-py's ``StockDataStream`` WebSocket to deliver live minute bars
(and optionally quotes/trades) to registered handlers. Paper/free accounts
only have access to the IEX feed, which is the default.

If alpaca-py's live module isn't importable, the client degrades gracefully
to a connected-but-idle state so the rest of the bot keeps running on REST
polling.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.brokers.base import BaseBrokerStreaming, MessageHandler
from app.config.settings import Settings
from app.monitoring import get_logger

logger = get_logger(__name__)


class AlpacaStreaming(BaseBrokerStreaming):
    """WebSocket streaming for Alpaca market data."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.alpaca_api_key
        self._secret_key = settings.alpaca_secret_key
        self._paper = settings.alpaca_paper
        self._feed_name = (getattr(settings, "alpaca_data_feed", "") or "iex").lower()
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._connected = False
        self._stream: Any = None
        self._pending_quotes: list[str] = []
        self._pending_bars: list[str] = []
        self._pending_trades: list[str] = []
        self._subscribe_orders = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Subscriptions ───────────────────────────────────────────────────

    async def subscribe_quotes(self, symbols: list[str]) -> None:
        new = [s for s in symbols if s not in self._pending_quotes]
        if not new:
            return
        self._pending_quotes.extend(new)
        await self._apply_subscription_change()

    async def subscribe_bars(self, symbols: list[str]) -> None:
        new = [s for s in symbols if s not in self._pending_bars]
        if not new:
            return
        self._pending_bars.extend(new)
        logger.info("alpaca_bars_subscribed", symbols=new)
        await self._apply_subscription_change()

    async def subscribe_trades(self, symbols: list[str]) -> None:
        new = [s for s in symbols if s not in self._pending_trades]
        if not new:
            return
        self._pending_trades.extend(new)
        await self._apply_subscription_change()

    async def unsubscribe_bars(self, symbols: list[str]) -> None:
        removed = [s for s in symbols if s in self._pending_bars]
        for s in removed:
            self._pending_bars.remove(s)
        if removed:
            logger.info("alpaca_bars_unsubscribed", symbols=removed)
            await self._apply_subscription_change()

    async def _apply_subscription_change(self) -> None:
        """Apply a subscription change to a (possibly running) stream safely.

        alpaca-py's live ``subscribe_*`` / ``unsubscribe_*`` methods block the
        event loop when called on an already-running socket from the loop thread
        (they wait on an internal future that can't resolve while the loop is
        blocked) — a deadlock that silently freezes the whole bot. To avoid it
        entirely we never mutate a live stream in place: we bounce the socket so
        ``connect()``'s reconnect loop rebuilds a fresh stream subscribed to the
        updated pending set. The new subscriptions are picked up on reconnect.
        """
        if self._stream is not None and self._connected:
            try:
                await self._stream.stop_ws()
                logger.info("alpaca_stream_resubscribe_bounce")
            except Exception as exc:
                logger.warning("alpaca_resubscribe_bounce_failed", error=str(exc))

    async def subscribe_order_updates(self) -> None:
        self._subscribe_orders = True

    def on(self, event_type: str, handler: MessageHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    # ── Connection ──────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the WebSocket and run it (with reconnection) until disconnected."""
        try:
            from alpaca.data.enums import DataFeed
            from alpaca.data.live import StockDataStream
        except ImportError:
            logger.warning("alpaca_streaming_unavailable_polling_only")
            self._connected = True
            while self._connected:
                await asyncio.sleep(60)
            return

        feed = DataFeed.SIP if self._feed_name == "sip" else DataFeed.IEX
        self._connected = True

        def _build_stream() -> Any:
            stream = StockDataStream(self._api_key, self._secret_key, feed=feed)
            bars = list(dict.fromkeys(self._pending_bars))
            quotes = list(dict.fromkeys(self._pending_quotes))
            trades = list(dict.fromkeys(self._pending_trades))
            if bars:
                stream.subscribe_bars(self._handle_bar, *bars)
            if quotes:
                stream.subscribe_quotes(self._handle_quote, *quotes)
            if trades:
                stream.subscribe_trades(self._handle_trade, *trades)
            logger.info(
                "alpaca_streaming_started",
                bars=len(bars),
                quotes=len(quotes),
                trades=len(trades),
                feed=feed.value,
            )
            return stream

        # _run_forever() manages the socket; wrap in a retry loop so a transient
        # disconnect (whether it raises or just returns) doesn't kill the data
        # feed for the rest of the session. A fresh stream is built on each
        # attempt and re-subscribed to the current symbol set.
        while self._connected:
            self._stream = _build_stream()
            try:
                await self._stream._run_forever()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._connected:
                    break
                logger.error("alpaca_streaming_error", error=str(exc))
            if self._connected:
                logger.warning("alpaca_streaming_reconnecting")
                await asyncio.sleep(5)

    async def disconnect(self) -> None:
        self._connected = False
        if self._stream is not None:
            try:
                await self._stream.stop_ws()
            except Exception as exc:
                logger.warning("alpaca_streaming_stop_error", error=str(exc))
        logger.info("alpaca_streaming_stopped")

    # ── Internal handlers (alpaca-py models → dicts → registered handlers) ─

    async def _handle_bar(self, bar: Any) -> None:
        await self._dispatch("bar", {
            "symbol": bar.symbol,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": int(bar.volume or 0),
            "timestamp": bar.timestamp,
            "vwap": float(bar.vwap) if getattr(bar, "vwap", None) else None,
        })

    async def _handle_quote(self, q: Any) -> None:
        await self._dispatch("quote", {
            "symbol": q.symbol,
            "bid": float(q.bid_price or 0),
            "ask": float(q.ask_price or 0),
            "bid_size": int(q.bid_size or 0),
            "ask_size": int(q.ask_size or 0),
            "timestamp": q.timestamp,
        })

    async def _handle_trade(self, t: Any) -> None:
        await self._dispatch("trade", {
            "symbol": t.symbol,
            "price": float(t.price or 0),
            "size": int(t.size or 0),
            "timestamp": t.timestamp,
        })

    async def _dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        for handler in self._handlers.get(event_type, []):
            try:
                await handler(data)
            except Exception as exc:
                logger.error("streaming_handler_error", event=event_type, error=str(exc))
