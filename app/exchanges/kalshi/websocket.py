"""
Kalshi WebSocket client implementing the BaseWebSocketClient interface.

Connects to Kalshi's trade-api/ws/v2 endpoint and subscribes to channels:
  - ticker:              market price snapshots
  - orderbook_delta:     incremental orderbook updates
  - trade:               recent trade feed
  - fill:                user fill notifications      (authenticated)
  - order_group_updates: user order status changes     (authenticated)

The client:
  - normalizes all Kalshi-specific payloads before forwarding to handlers
  - maintains a per-ticker book state so deltas can be applied incrementally
  - sends periodic keep-alive pings (required by Kalshi)
  - automatically reconnects with exponential back-off
  - detects stale feeds and emits metrics
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from app.config.settings import Settings
from app.exchanges.base import BaseWebSocketClient, MessageHandler
from app.exchanges.kalshi.auth import KalshiAuth
from app.exchanges.kalshi.normalizer import (
    normalize_fill,
    normalize_order_update,
    normalize_orderbook,
    normalize_orderbook_delta,
    normalize_trade,
)
from app.data.models import OrderbookSnapshot
from app.monitoring import get_logger
from app.monitoring.logger import metrics

logger = get_logger(__name__)

HEARTBEAT_INTERVAL = 10.0
STALE_THRESHOLD = 60.0
RECONNECT_BASE_DELAY = 1.0
RECONNECT_MAX_DELAY = 60.0
MAX_CONSECUTIVE_RECONNECTS = 50

DEMO_WS_URL = "wss://demo-api.kalshi.co/trade-api/ws/v2"


class KalshiWebSocketClient(BaseWebSocketClient):
    """WebSocket client for Kalshi real-time data."""

    def __init__(self, settings: Settings) -> None:
        self._ws_url = DEMO_WS_URL if settings.kalshi_demo_mode else settings.kalshi_ws_url
        self._auth: KalshiAuth | None = None

        if settings.kalshi_api_key and (settings.kalshi_private_key or settings.kalshi_private_key_path):
            self._auth = KalshiAuth(
                settings.kalshi_api_key,
                settings.kalshi_private_key_path,
                private_key_pem=settings.kalshi_private_key,
            )

        self._ws: Any = None
        self._subscriptions: list[dict[str, Any]] = []
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._running = False
        self._last_message_time: datetime | None = None
        self._reconnect_count = 0
        self._command_id = 0

        self._book_snapshots: dict[str, OrderbookSnapshot] = {}

    # ── Public properties ─────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.open

    @property
    def seconds_since_last_message(self) -> float:
        if self._last_message_time is None:
            return float("inf")
        delta = datetime.now(timezone.utc) - self._last_message_time
        return delta.total_seconds()

    @property
    def is_stale(self) -> bool:
        return self.seconds_since_last_message > STALE_THRESHOLD

    # ── Handler registration ──────────────────────────────────────────

    def on(self, event_type: str, handler: MessageHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    # ── Subscription helpers ──────────────────────────────────────────

    def _next_id(self) -> int:
        self._command_id += 1
        return self._command_id

    def subscribe_book(self, instrument_ids: list[str]) -> None:
        if not instrument_ids:
            return
        sub = {
            "id": self._next_id(),
            "cmd": "subscribe",
            "params": {"channels": ["orderbook_delta"], "market_tickers": list(instrument_ids)},
        }
        self._subscriptions.append(sub)

    def subscribe_trades(self, instrument_ids: list[str]) -> None:
        if not instrument_ids:
            return
        sub = {
            "id": self._next_id(),
            "cmd": "subscribe",
            "params": {"channels": ["trade"], "market_tickers": list(instrument_ids)},
        }
        self._subscriptions.append(sub)

    def subscribe_ticker(self, instrument_ids: list[str]) -> None:
        """Subscribe to ticker (market price snapshot) updates."""
        if not instrument_ids:
            return
        sub = {
            "id": self._next_id(),
            "cmd": "subscribe",
            "params": {"channels": ["ticker"], "market_tickers": list(instrument_ids)},
        }
        self._subscriptions.append(sub)

    def subscribe_user(self) -> None:
        if self._auth is None:
            logger.warning("kalshi_ws_user_sub_skipped_no_auth")
            return
        sub = {
            "id": self._next_id(),
            "cmd": "subscribe",
            "params": {"channels": ["fill", "order_group_updates"]},
        }
        self._subscriptions.append(sub)

    # ── Connection lifecycle ──────────────────────────────────────────

    async def connect(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._run_connection()
            except asyncio.CancelledError:
                break
            except Exception as e:
                metrics.increment("ws_reconnects")
                self._reconnect_count += 1

                if self._reconnect_count > MAX_CONSECUTIVE_RECONNECTS:
                    logger.critical(
                        "kalshi_ws_max_reconnects_exceeded",
                        count=self._reconnect_count,
                    )
                    self._running = False
                    break

                delay = min(
                    RECONNECT_BASE_DELAY * (2 ** min(self._reconnect_count, 6)),
                    RECONNECT_MAX_DELAY,
                )
                logger.warning(
                    "kalshi_ws_reconnecting",
                    error=str(e),
                    reconnect_count=self._reconnect_count,
                    delay=delay,
                )
                await asyncio.sleep(delay)

    async def _run_connection(self) -> None:
        extra_headers: dict[str, str] = {}
        if self._auth is not None:
            extra_headers = self._auth.sign_request("GET", "/trade-api/ws/v2")

        async with websockets.connect(
            self._ws_url,
            additional_headers=extra_headers,
            ping_interval=20,
            ping_timeout=30,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._reconnect_count = 0
            self._book_snapshots.clear()
            logger.info("kalshi_ws_connected", url=self._ws_url)

            for sub in self._subscriptions:
                await ws.send(json.dumps(sub))
                logger.debug("kalshi_ws_subscribed", channels=sub.get("params", {}).get("channels"))

            heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            try:
                async for raw_msg in ws:
                    self._last_message_time = datetime.now(timezone.utc)
                    metrics.increment("ws_messages_received")
                    try:
                        msg = json.loads(raw_msg)
                        await self._dispatch(msg)
                    except json.JSONDecodeError:
                        logger.warning("kalshi_ws_invalid_json", data=str(raw_msg)[:200])
            except ConnectionClosed as cc:
                logger.warning("kalshi_ws_connection_closed", code=cc.code, reason=cc.reason[:120] if cc.reason else "")
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _heartbeat_loop(self) -> None:
        """Periodic keep-alive and stale-data detection."""
        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if self._ws and self._ws.open:
                try:
                    await self._ws.ping()
                except Exception:
                    logger.warning("kalshi_ws_ping_failed")
            if self.is_stale:
                logger.warning("kalshi_ws_stale_data", seconds=self.seconds_since_last_message)
                metrics.increment("ws_stale_detected")

    # ── Message dispatch ──────────────────────────────────────────────

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        """Route and normalize incoming WS messages to registered handlers."""
        msg_type = msg.get("type", "")
        channel = msg.get("channel", msg_type)

        if msg_type in ("subscribed", "unsubscribed"):
            logger.debug("kalshi_ws_ctrl", type=msg_type, sid=msg.get("sid"))
            return
        if "sid" in msg and not channel:
            return

        if channel == "orderbook_delta":
            await self._handle_orderbook(msg)
        elif channel == "orderbook_snapshot":
            await self._handle_orderbook(msg)
        elif channel == "trade":
            await self._handle_trade(msg)
        elif channel == "fill":
            await self._handle_fill(msg)
        elif channel == "order_group_updates":
            await self._handle_order_update(msg)
        elif channel == "ticker":
            await self._handle_ticker(msg)
        else:
            logger.debug("kalshi_ws_unknown_channel", channel=channel, keys=list(msg.keys())[:10])

    async def _handle_orderbook(self, msg: dict[str, Any]) -> None:
        """Normalize orderbook snapshot or delta and dispatch to 'book' handlers."""
        data = msg.get("msg", msg)
        ticker = data.get("market_ticker", data.get("ticker", ""))
        if not ticker:
            return

        channel = msg.get("type", msg.get("channel", ""))
        is_snapshot = channel == "orderbook_snapshot"
        if is_snapshot:
            book = normalize_orderbook(ticker, data)
            self._book_snapshots[ticker] = book
        else:
            existing = self._book_snapshots.get(ticker)
            book = normalize_orderbook_delta(ticker, data, existing)
            self._book_snapshots[ticker] = book

        normalized: dict[str, Any] = {
            "type": "snapshot",
            "market_ticker": ticker,
            "instrument_id": ticker,
            "exchange": "kalshi",
            "assets": [
                {
                    "asset_id": ticker,
                    "instrument_id": ticker,
                    "market_ticker": ticker,
                    "type": "snapshot",
                    "bids": [[l.price, l.size] for l in book.bids],
                    "asks": [[l.price, l.size] for l in book.asks],
                }
            ],
        }

        for handler in self._handlers.get("book", []):
            try:
                await handler(normalized)
            except Exception as e:
                logger.error("kalshi_ws_book_handler_error", error=str(e))
                metrics.increment("ws_handler_errors")

    async def _handle_trade(self, msg: dict[str, Any]) -> None:
        """Normalize trade messages and dispatch to 'trade' handlers."""
        raw_trades = msg.get("msg", {}).get("trades", [])
        if not raw_trades:
            raw_trades = [msg.get("msg", msg)]

        normalized_trades: list[dict[str, Any]] = []
        for raw_t in raw_trades:
            trade = normalize_trade(raw_t)
            normalized_trades.append({
                "asset_id": trade.instrument_id,
                "instrument_id": trade.instrument_id,
                "market_ticker": trade.instrument_id,
                "exchange": "kalshi",
                "price": trade.price,
                "size": trade.size,
                "side": trade.side.value,
                "timestamp": (trade.timestamp.isoformat() if hasattr(trade.timestamp, "isoformat") else str(trade.timestamp)) if trade.timestamp else None,
            })

        normalized: dict[str, Any] = {
            "type": "trade",
            "exchange": "kalshi",
            "trades": normalized_trades,
        }

        for handler in self._handlers.get("trade", []):
            try:
                await handler(normalized)
            except Exception as e:
                logger.error("kalshi_ws_trade_handler_error", error=str(e))
                metrics.increment("ws_handler_errors")

    async def _handle_fill(self, msg: dict[str, Any]) -> None:
        """Normalize fill messages and dispatch to 'user' handlers."""
        raw_fills = msg.get("msg", {}).get("fills", [])
        if not raw_fills:
            raw_fills = [msg.get("msg", msg)]

        for raw_f in raw_fills:
            fill = normalize_fill(raw_f)
            normalized: dict[str, Any] = {
                "type": "fill",
                "event": "fill",
                "exchange": "kalshi",
                **fill,
            }

            for handler in self._handlers.get("user", []):
                try:
                    await handler(normalized)
                except Exception as e:
                    logger.error("kalshi_ws_fill_handler_error", error=str(e))
                    metrics.increment("ws_handler_errors")

    async def _handle_order_update(self, msg: dict[str, Any]) -> None:
        """Normalize order-status update messages and dispatch to 'user' handlers."""
        raw_orders = msg.get("msg", {}).get("orders", [])
        if not raw_orders:
            raw_orders = [msg.get("msg", msg)]

        for raw_o in raw_orders:
            order_info = normalize_order_update(raw_o)
            normalized: dict[str, Any] = {
                "type": "order_update",
                "event": "order_update",
                "exchange": "kalshi",
                **order_info,
            }

            for handler in self._handlers.get("user", []):
                try:
                    await handler(normalized)
                except Exception as e:
                    logger.error("kalshi_ws_order_handler_error", error=str(e))
                    metrics.increment("ws_handler_errors")

    async def _handle_ticker(self, msg: dict[str, Any]) -> None:
        """Forward ticker updates with price normalization."""
        data = msg.get("msg", msg)
        ticker = data.get("market_ticker", data.get("ticker", ""))
        if not ticker:
            return

        from app.exchanges.kalshi.normalizer import cents_to_decimal as _c2d

        normalized: dict[str, Any] = {
            "type": "ticker",
            "exchange": "kalshi",
            "instrument_id": ticker,
            "market_ticker": ticker,
            "yes_price": _c2d(data.get("yes_price", 50)),
            "no_price": _c2d(data.get("no_price", 50)),
            "volume": data.get("volume"),
            "open_interest": data.get("open_interest"),
        }

        for handler in self._handlers.get("ticker", []):
            try:
                await handler(normalized)
            except Exception as e:
                logger.error("kalshi_ws_ticker_handler_error", error=str(e))
                metrics.increment("ws_handler_errors")

    # ── Disconnect ────────────────────────────────────────────────────

    async def disconnect(self) -> None:
        self._running = False
        if self._ws and self._ws.open:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self._book_snapshots.clear()
        logger.info("kalshi_ws_disconnected")
