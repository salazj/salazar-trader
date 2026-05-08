"""Stock execution engine — routes approved signals to the broker."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.brokers.base import BaseBrokerExecution
from app.config.settings import Settings
from app.data.models import PortfolioSnapshot
from app.models.enums import OrderType, StockAction
from app.monitoring import get_logger
from app.stocks.models import StockFeatures, StockSignal
from app.stocks.risk import StockRiskCheckResult, StockRiskManager

logger = get_logger(__name__)


class StockOrderRecord:
    """In-memory record of a stock order."""

    __slots__ = (
        "order_id", "symbol", "side", "price", "quantity",
        "order_type", "status", "broker_order_id", "filled_quantity",
        "created_at", "updated_at",
    )

    def __init__(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: int,
        order_type: str = "market",
    ) -> None:
        self.order_id = str(uuid.uuid4())
        self.symbol = symbol
        self.side = side
        self.price = price
        self.quantity = quantity
        self.order_type = order_type
        self.status = "pending"
        self.broker_order_id = ""
        self.filled_quantity = 0
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = self.created_at


class StockExecutionEngine:
    """Manages stock order lifecycle."""

    def __init__(
        self,
        settings: Settings,
        execution_client: BaseBrokerExecution,
        risk_manager: StockRiskManager,
    ) -> None:
        self._settings = settings
        self._execution = execution_client
        self._risk = risk_manager
        self._active_orders: dict[str, StockOrderRecord] = {}
        self._order_history: list[StockOrderRecord] = []
        self._lock = threading.Lock()

    @property
    def active_orders(self) -> list[StockOrderRecord]:
        with self._lock:
            return [o for o in self._active_orders.values() if o.status == "pending"]

    async def process_signal(
        self,
        signal: StockSignal,
        features: StockFeatures,
        portfolio: PortfolioSnapshot,
        *,
        broker=None,
    ) -> StockOrderRecord | None:
        if signal.action == StockAction.HOLD:
            return None

        side = "buy" if signal.action in (StockAction.BUY, StockAction.COVER) else "sell"
        price = signal.suggested_price or features.last_price
        quantity = signal.suggested_quantity or self._compute_quantity(price, portfolio)

        if quantity <= 0:
            return None

        check = self._risk.check_order(
            symbol=signal.symbol,
            side=side,
            price=price,
            quantity=quantity,
            portfolio=portfolio,
            broker=broker,
            stop_price=signal.stop_price,
            bar_timestamp=features.timestamp,
        )
        if not check.approved:
            logger.info(
                "stock_order_rejected",
                symbol=signal.symbol,
                reason=check.reason,
            )
            return None

        record = StockOrderRecord(
            symbol=signal.symbol,
            side=side,
            price=price,
            quantity=quantity,
            order_type=signal.order_type.value,
        )

        if self._execution.is_dry_run:
            record.status = "filled"
            record.filled_quantity = quantity
            record.broker_order_id = f"dry-{record.order_id[:8]}"
            logger.info(
                "stock_order_dry_fill",
                symbol=signal.symbol,
                side=side,
                price=price,
                quantity=quantity,
            )
        else:
            try:
                result = await self._execution.place_order(
                    symbol=signal.symbol,
                    side=side,
                    quantity=float(quantity),
                    order_type=signal.order_type,
                    price=price if signal.order_type != OrderType.MARKET else None,
                    stop_price=signal.stop_price,
                )
                record.broker_order_id = result.get("id", "")
                record.status = result.get("status", "pending")
                logger.info(
                    "stock_order_placed",
                    symbol=signal.symbol,
                    side=side,
                    price=price,
                    quantity=quantity,
                    broker_id=record.broker_order_id,
                )
            except Exception as exc:
                record.status = "rejected"
                logger.error(
                    "stock_order_failed",
                    symbol=signal.symbol,
                    error=str(exc),
                )

        with self._lock:
            self._active_orders[record.order_id] = record
            self._order_history.append(record)

        return record

    async def cancel_all_orders(self) -> int:
        try:
            await self._execution.cancel_all()
        except Exception as exc:
            logger.error("stock_cancel_all_failed", error=str(exc))
        count = 0
        with self._lock:
            for o in self._active_orders.values():
                if o.status == "pending":
                    o.status = "canceled"
                    count += 1
        return count

    def _compute_quantity(self, price: float, portfolio: PortfolioSnapshot) -> int:
        if price <= 0:
            return 0
        max_dollars = min(
            self._settings.stock_max_position_dollars,
            portfolio.cash * 0.25,
        )
        return max(1, int(max_dollars / price))
