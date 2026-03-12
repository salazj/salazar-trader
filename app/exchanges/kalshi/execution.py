"""
Kalshi execution client implementing the BaseExecutionClient interface.

Uses Kalshi REST API v2 for authenticated order operations.

Kalshi order model:
  - ``action``:  "buy" | "sell"   — whether you're entering or exiting
  - ``side``:    "yes" | "no"     — which outcome you're trading
  - ``type``:    "limit" | "market"
  - ``yes_price`` / ``no_price``:  price in cents (0-100)

This client maps the internal Side (BUY/SELL) plus the Order's metadata into
the correct Kalshi action/side/price triple.  All live-trading paths are
gated behind DRY_RUN + ENABLE_LIVE_TRADING + LIVE_TRADING_ACKNOWLEDGED.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config.settings import Settings
from app.data.models import Order, OrderStatus, Side
from app.exchanges.base import BaseExecutionClient
from app.exchanges.kalshi.auth import KalshiAuth
from app.exchanges.kalshi.normalizer import (
    cents_to_decimal,
    decimal_to_cents,
    normalize_order_update,
    normalize_position,
)
from app.monitoring import get_logger
from app.monitoring.logger import metrics
from app.utils.helpers import generate_order_id

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"


class KalshiExecutionClient(BaseExecutionClient):
    """Order execution for Kalshi via REST API v2."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dry_run = settings.dry_run or not settings.enable_live_trading

        base_url = DEMO_BASE_URL if settings.kalshi_demo_mode else settings.kalshi_base_url
        self._base_url = base_url
        self._auth: KalshiAuth | None = None
        self._client: httpx.AsyncClient | None = None

        if not self._dry_run and settings.kalshi_api_key and (settings.kalshi_private_key or settings.kalshi_private_key_path):
            self._auth = KalshiAuth(
                settings.kalshi_api_key,
                settings.kalshi_private_key_path,
                private_key_pem=settings.kalshi_private_key,
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=DEFAULT_TIMEOUT,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            logger.info("kalshi_live_client_initialized", base_url=self._base_url)

    @property
    def is_dry_run(self) -> bool:
        return self._dry_run

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if self._auth is None:
            return {}
        return self._auth.sign_request(method, path)

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _request(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Kalshi live client not initialized — check credentials")
        headers = self._auth_headers(method, path)
        resp = await self._client.request(method, path, json=json_body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ── Order placement ───────────────────────────────────────────────

    async def place_order(self, order: Order) -> Order:
        logger.info(
            "placing_kalshi_order",
            order_id=order.order_id,
            instrument_id=order.instrument_id,
            side=order.side.value,
            price=order.price,
            size=order.size,
            dry_run=self._dry_run,
        )

        if self._dry_run:
            return self._simulate_place(order)
        return await self._live_place(order)

    def _simulate_place(self, order: Order) -> Order:
        order.status = OrderStatus.ACKNOWLEDGED
        order.exchange_order_id = f"DRY-KALSHI-{generate_order_id()}"
        metrics.increment("orders_placed_dry")
        logger.info("kalshi_dry_run_order", order_id=order.order_id)
        return order

    def _build_order_body(self, order: Order) -> dict[str, Any]:
        """Translate internal Order fields into Kalshi's order-create payload.

        Kalshi uses ``action`` × ``side`` where:
          - action=buy,  side=yes  → buying YES contracts
          - action=sell, side=yes  → selling YES contracts you own
          - action=buy,  side=no   → buying NO contracts
          - action=sell, side=no   → selling NO contracts you own

        The instrument_id may end with "-no" to signal a NO-side trade.
        """
        ticker = order.instrument_id
        is_no_side = ticker.endswith("-no")
        if is_no_side:
            ticker = ticker[:-3]

        if order.side == Side.BUY:
            action = "buy"
        else:
            action = "sell"

        kalshi_side = "no" if is_no_side else "yes"

        if is_no_side:
            price_cents = decimal_to_cents(1.0 - order.price)
        else:
            price_cents = decimal_to_cents(order.price)

        price_cents = max(1, min(99, price_cents))

        body: dict[str, Any] = {
            "ticker": ticker,
            "action": action,
            "side": kalshi_side,
            "type": "limit",
            "count": int(order.size),
        }
        if kalshi_side == "yes":
            body["yes_price"] = price_cents
        else:
            body["no_price"] = price_cents

        if order.order_id:
            body["client_order_id"] = order.order_id

        return body

    async def _live_place(self, order: Order) -> Order:
        if self._settings.dry_run or not self._settings.enable_live_trading:
            logger.critical("KALSHI_LIVE_ORDER_BLOCKED_safety_recheck", order_id=order.order_id)
            order.status = OrderStatus.REJECTED
            return order

        if not self._settings.live_trading_acknowledged:
            logger.critical("KALSHI_LIVE_ORDER_BLOCKED_not_acknowledged", order_id=order.order_id)
            order.status = OrderStatus.REJECTED
            return order

        try:
            body = self._build_order_body(order)
            data = await self._request("POST", "/portfolio/orders", json_body=body)

            order_resp = data.get("order", {})
            exchange_id = order_resp.get("order_id", "")
            kalshi_status = order_resp.get("status", "")

            if exchange_id:
                order.exchange_order_id = exchange_id
                from app.exchanges.kalshi.normalizer import normalize_order_status
                mapped = normalize_order_status(kalshi_status)
                order.status = mapped if mapped else OrderStatus.ACKNOWLEDGED
                metrics.increment("orders_placed_live")
                logger.info(
                    "kalshi_live_order_placed",
                    order_id=order.order_id,
                    exchange_id=exchange_id,
                    kalshi_status=kalshi_status,
                )
            else:
                order.status = OrderStatus.REJECTED
                metrics.increment("orders_rejected")
                logger.warning("kalshi_live_order_rejected", response=str(data)[:300])

        except httpx.HTTPStatusError as e:
            order.status = OrderStatus.REJECTED
            metrics.increment("orders_rejected")
            body_text = e.response.text[:300] if e.response else ""
            logger.error(
                "kalshi_order_http_error",
                order_id=order.order_id,
                status=e.response.status_code if e.response else 0,
                body=body_text,
            )
        except Exception as e:
            order.status = OrderStatus.REJECTED
            metrics.increment("orders_rejected")
            logger.error("kalshi_order_error", order_id=order.order_id, error=str(e))

        return order

    # ── Order cancellation ────────────────────────────────────────────

    async def cancel_order(self, order: Order) -> Order:
        logger.info("kalshi_canceling_order", order_id=order.order_id)

        if self._dry_run:
            order.status = OrderStatus.CANCELED
            metrics.increment("orders_canceled_dry")
            return order

        if not order.exchange_order_id:
            logger.warning("kalshi_cannot_cancel_no_exchange_id", order_id=order.order_id)
            return order

        try:
            await self._request("DELETE", f"/portfolio/orders/{order.exchange_order_id}")
            order.status = OrderStatus.CANCELED
            metrics.increment("orders_canceled_live")
            logger.info("kalshi_order_canceled", order_id=order.order_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                order.status = OrderStatus.CANCELED
                logger.info("kalshi_cancel_already_gone", order_id=order.order_id)
            else:
                logger.error("kalshi_cancel_http_error", order_id=order.order_id, status=e.response.status_code)
        except Exception as e:
            logger.error("kalshi_cancel_error", order_id=order.order_id, error=str(e))

        return order

    async def cancel_all(self) -> None:
        logger.info("kalshi_canceling_all_orders", dry_run=self._dry_run)
        if self._dry_run:
            return
        try:
            await self._request("DELETE", "/portfolio/orders")
            metrics.increment("cancel_all_invoked")
            logger.info("kalshi_all_orders_canceled")
        except Exception as e:
            logger.error("kalshi_cancel_all_error", error=str(e))

    # ── Account queries ───────────────────────────────────────────────

    async def get_balance(self) -> float:
        if self._dry_run:
            return 0.0
        try:
            data = await self._request("GET", "/portfolio/balance")
            balance_cents = data.get("balance", 0)
            return cents_to_decimal(balance_cents)
        except Exception as e:
            logger.warning("kalshi_balance_error", error=str(e))
            return 0.0

    async def get_open_positions(self) -> list[dict[str, Any]]:
        if self._dry_run:
            return []
        try:
            data = await self._request("GET", "/portfolio/positions")
            raw_positions = data.get("market_positions", [])
            return [normalize_position(p) for p in raw_positions]
        except Exception as e:
            logger.warning("kalshi_positions_error", error=str(e))
            return []

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Query currently resting orders from Kalshi."""
        if self._dry_run:
            return []
        try:
            data = await self._request("GET", "/portfolio/orders")
            raw_orders = data.get("orders", [])
            return [normalize_order_update(o) for o in raw_orders]
        except Exception as e:
            logger.warning("kalshi_open_orders_error", error=str(e))
            return []

    async def get_fills(self, ticker: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Query historical fills from Kalshi."""
        if self._dry_run:
            return []
        try:
            params: dict[str, Any] = {"limit": limit}
            if ticker:
                params["ticker"] = ticker
            data = await self._request("GET", "/portfolio/fills")
            raw_fills = data.get("fills", [])
            from app.exchanges.kalshi.normalizer import normalize_fill
            return [normalize_fill(f) for f in raw_fills]
        except Exception as e:
            logger.warning("kalshi_fills_error", error=str(e))
            return []

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
