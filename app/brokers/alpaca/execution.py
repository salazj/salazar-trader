"""Alpaca order execution client."""

from __future__ import annotations

from typing import Any

from app.brokers.base import BaseBrokerExecution
from app.config.settings import Settings
from app.models.enums import OrderType, TimeInForce
from app.monitoring import get_logger

logger = get_logger(__name__)


class AlpacaExecution(BaseBrokerExecution):
    """Order execution for Alpaca."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.alpaca_api_key
        self._secret_key = settings.alpaca_secret_key
        self._paper = settings.alpaca_paper
        self._dry_run = settings.dry_run
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from alpaca.trading.client import TradingClient
                self._client = TradingClient(
                    self._api_key, self._secret_key, paper=self._paper
                )
            except ImportError:
                logger.warning("alpaca-py not installed")
        return self._client

    @property
    def is_dry_run(self) -> bool:
        return self._dry_run

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
        if self._dry_run:
            import uuid
            return {
                "id": f"dry-{uuid.uuid4().hex[:8]}",
                "symbol": symbol,
                "side": side,
                "qty": quantity,
                "status": "filled",
            }

        client = self._ensure_client()
        if client is None:
            raise RuntimeError("Alpaca client not initialized")

        try:
            from alpaca.trading.requests import (
                LimitOrderRequest,
                MarketOrderRequest,
                StopLimitOrderRequest,
                StopOrderRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce as AlpacaTIF

            alpaca_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            alpaca_tif = AlpacaTIF.DAY

            if order_type == OrderType.MARKET:
                req = MarketOrderRequest(
                    symbol=symbol, qty=quantity, side=alpaca_side, time_in_force=alpaca_tif
                )
            elif order_type == OrderType.LIMIT and price is not None:
                req = LimitOrderRequest(
                    symbol=symbol, qty=quantity, side=alpaca_side,
                    time_in_force=alpaca_tif, limit_price=price
                )
            elif order_type == OrderType.STOP and stop_price is not None:
                req = StopOrderRequest(
                    symbol=symbol, qty=quantity, side=alpaca_side,
                    time_in_force=alpaca_tif, stop_price=stop_price
                )
            elif order_type == OrderType.STOP_LIMIT and price and stop_price:
                req = StopLimitOrderRequest(
                    symbol=symbol, qty=quantity, side=alpaca_side,
                    time_in_force=alpaca_tif, limit_price=price, stop_price=stop_price
                )
            else:
                req = MarketOrderRequest(
                    symbol=symbol, qty=quantity, side=alpaca_side, time_in_force=alpaca_tif
                )

            order = client.submit_order(req)
            return {
                "id": str(order.id),
                "symbol": order.symbol,
                "side": order.side.value,
                "qty": float(order.qty) if order.qty else quantity,
                "status": order.status.value if order.status else "new",
                "type": order.order_type.value if order.order_type else order_type.value,
            }
        except Exception as exc:
            logger.error("alpaca_order_error", symbol=symbol, error=str(exc))
            raise

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
        """Submit an entry with broker-side protective legs.

        - both stop + target  → BRACKET (entry, take-profit, stop-loss)
        - only one leg         → OTO (entry + the single protective leg)
        - neither              → plain entry
        """
        if stop_price is None and take_profit_price is None:
            return await self.place_order(
                symbol=symbol, side=side, quantity=quantity,
                order_type=order_type, price=price, time_in_force=time_in_force,
            )

        if self._dry_run:
            import uuid
            return {
                "id": f"dry-{uuid.uuid4().hex[:8]}",
                "symbol": symbol, "side": side, "qty": quantity,
                "status": "filled", "order_class": "bracket",
                "stop_price": stop_price, "take_profit_price": take_profit_price,
            }

        client = self._ensure_client()
        if client is None:
            raise RuntimeError("Alpaca client not initialized")

        try:
            from alpaca.trading.requests import (
                LimitOrderRequest,
                MarketOrderRequest,
                StopLossRequest,
                TakeProfitRequest,
            )
            from alpaca.trading.enums import (
                OrderClass,
                OrderSide,
                TimeInForce as AlpacaTIF,
            )

            alpaca_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            alpaca_tif = AlpacaTIF.DAY
            take_profit = (
                TakeProfitRequest(limit_price=round(float(take_profit_price), 2))
                if take_profit_price is not None
                else None
            )
            stop_loss = (
                StopLossRequest(stop_price=round(float(stop_price), 2))
                if stop_price is not None
                else None
            )
            order_class = (
                OrderClass.BRACKET
                if take_profit and stop_loss
                else OrderClass.OTO
            )

            common = dict(
                symbol=symbol, qty=quantity, side=alpaca_side,
                time_in_force=alpaca_tif, order_class=order_class,
                take_profit=take_profit, stop_loss=stop_loss,
            )
            if order_type == OrderType.LIMIT and price is not None:
                req = LimitOrderRequest(limit_price=round(float(price), 2), **common)
            else:
                req = MarketOrderRequest(**common)

            order = client.submit_order(req)
            return {
                "id": str(order.id),
                "symbol": order.symbol,
                "side": order.side.value,
                "qty": float(order.qty) if order.qty else quantity,
                "status": order.status.value if order.status else "new",
                "order_class": order_class.value,
                "stop_price": stop_price,
                "take_profit_price": take_profit_price,
            }
        except Exception as exc:
            logger.error("alpaca_bracket_order_error", symbol=symbol, error=str(exc))
            raise

    async def close_position(self, symbol: str) -> dict[str, Any]:
        if self._dry_run:
            return {"symbol": symbol, "status": "closed"}
        client = self._ensure_client()
        if client is None:
            return {"symbol": symbol, "status": "unsupported"}
        try:
            order = client.close_position(symbol)
            return {
                "symbol": symbol,
                "id": str(getattr(order, "id", "")),
                "status": getattr(getattr(order, "status", None), "value", "closing"),
            }
        except Exception as exc:
            logger.error("alpaca_close_position_error", symbol=symbol, error=str(exc))
            return {"symbol": symbol, "status": "error", "error": str(exc)}

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        client = self._ensure_client()
        if client is None:
            return {"id": order_id, "status": "canceled"}
        try:
            client.cancel_order_by_id(order_id)
            return {"id": order_id, "status": "canceled"}
        except Exception as exc:
            logger.error("alpaca_cancel_error", order_id=order_id, error=str(exc))
            raise

    async def cancel_all(self) -> None:
        client = self._ensure_client()
        if client:
            try:
                client.cancel_orders()
            except Exception as exc:
                logger.error("alpaca_cancel_all_error", error=str(exc))

    async def get_order(self, order_id: str) -> dict[str, Any]:
        client = self._ensure_client()
        if client is None:
            return {}
        try:
            order = client.get_order_by_id(order_id)
            return {
                "id": str(order.id),
                "status": order.status.value if order.status else "",
                "symbol": order.symbol,
                "side": order.side.value if order.side else "",
                "qty": float(order.qty) if order.qty else 0.0,
                "filled_qty": float(order.filled_qty) if order.filled_qty else 0.0,
                "filled_avg_price": (
                    float(order.filled_avg_price) if order.filled_avg_price else 0.0
                ),
            }
        except Exception as exc:
            logger.error("alpaca_get_order_error", error=str(exc))
            return {}

    async def get_open_orders(self) -> list[dict[str, Any]]:
        client = self._ensure_client()
        if client is None:
            return []
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            orders = client.get_orders(req)
            return [
                {"id": str(o.id), "symbol": o.symbol, "side": o.side.value, "status": o.status.value}
                for o in orders
            ]
        except Exception as exc:
            logger.error("alpaca_open_orders_error", error=str(exc))
            return []

    async def get_account(self) -> dict[str, Any]:
        client = self._ensure_client()
        if client is None:
            return {"cash": 0, "buying_power": 0, "portfolio_value": 0}
        try:
            acct = client.get_account()
            return {
                "account_id": str(acct.id),
                "status": acct.status.value if acct.status else "",
                "cash": float(acct.cash) if acct.cash else 0,
                "buying_power": float(acct.buying_power) if acct.buying_power else 0,
                "portfolio_value": float(acct.portfolio_value) if acct.portfolio_value else 0,
                "currency": acct.currency or "USD",
            }
        except Exception as exc:
            logger.error("alpaca_account_error", error=str(exc))
            return {"cash": 0, "buying_power": 0, "portfolio_value": 0}

    async def get_positions(self) -> list[dict[str, Any]]:
        client = self._ensure_client()
        if client is None:
            return []
        try:
            positions = client.get_all_positions()
            return [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty) if p.qty else 0,
                    "avg_entry_price": float(p.avg_entry_price) if p.avg_entry_price else 0,
                    "current_price": float(p.current_price) if p.current_price else 0,
                    "market_value": float(p.market_value) if p.market_value else 0,
                    "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else 0,
                    "side": p.side,
                }
                for p in positions
            ]
        except Exception as exc:
            logger.error("alpaca_positions_error", error=str(exc))
            return []

    async def close(self) -> None:
        self._client = None
