"""
Portfolio Tracker

Manages cash, positions, entry prices, realized PnL, and unrealized PnL.
Correctly handles Yes and No positions for binary outcome markets.
Supports mark-to-market from current best bid/ask.

Positions are keyed by instrument_id (exchange-agnostic identifier).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.config.settings import Settings
from app.data.models import (
    Order,
    OutcomeSide,
    Position,
    PortfolioSnapshot,
    Side,
)
from app.monitoring import get_logger
from app.utils.helpers import utc_now

logger = get_logger(__name__)


class PortfolioTracker:
    """Thread-safe portfolio state manager."""

    def __init__(self, settings: Settings, starting_cash: float | None = None) -> None:
        self._settings = settings
        self._cash = starting_cash if starting_cash is not None else 0.0
        self._initial_cash = self._cash
        self._positions: dict[str, Position] = {}  # keyed by instrument_id
        self._realized_pnl = 0.0
        self._daily_realized_pnl = 0.0
        self._daily_start_equity: float | None = None
        self._lock = threading.Lock()

    @property
    def cash(self) -> float:
        with self._lock:
            return self._cash

    @property
    def positions(self) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def get_position(self, instrument_id: str) -> Position | None:
        with self._lock:
            return self._positions.get(instrument_id)

    def get_snapshot(self) -> PortfolioSnapshot:
        with self._lock:
            positions = list(self._positions.values())
            total_exposure = sum(p.notional for p in positions)
            total_unrealized = sum(p.unrealized_pnl for p in positions)
            equity = self._cash + sum(p.market_value for p in positions)

            daily_pnl = 0.0
            if self._daily_start_equity is not None:
                daily_pnl = equity - self._daily_start_equity

            return PortfolioSnapshot(
                timestamp=utc_now(),
                cash=self._cash,
                positions=positions,
                total_exposure=total_exposure,
                total_unrealized_pnl=total_unrealized,
                total_realized_pnl=self._realized_pnl,
                daily_pnl=daily_pnl,
            )

    def on_fill(self, order: Order, fill_price: float, fill_size: float) -> float:
        """
        Process a fill event. Updates positions, cash, and PnL.
        Returns realized PnL for this fill (0 for buys).
        """
        realized = 0.0
        iid = order.instrument_id or order.token_id
        with self._lock:
            cost = fill_price * fill_size

            if order.side == Side.BUY:
                if cost > self._cash:
                    logger.warning(
                        "fill_exceeds_cash",
                        cost=cost,
                        cash=self._cash,
                        instrument_id=iid,
                    )
                self._cash -= cost
                self._add_to_position(order, fill_price, fill_size)
            elif order.side == Side.SELL:
                self._cash += cost
                realized = self._remove_from_position(order, fill_price, fill_size)
                self._realized_pnl += realized
                self._daily_realized_pnl += realized

        logger.info(
            "fill_processed",
            instrument_id=iid,
            side=order.side.value,
            price=fill_price,
            size=fill_size,
            realized_pnl=realized,
            cash=self._cash,
        )
        return realized

    def _add_to_position(self, order: Order, price: float, size: float) -> None:
        iid = order.instrument_id or order.token_id
        pos = self._positions.get(iid)
        if pos is None:
            pos = Position(
                market_id=order.market_id,
                token_id=iid,
                instrument_id=iid,
                exchange=order.exchange,
                token_side=OutcomeSide.YES,
            )
            self._positions[iid] = pos

        old_notional = pos.size * pos.avg_entry_price
        new_notional = size * price
        pos.size += size
        if pos.size > 0:
            pos.avg_entry_price = (old_notional + new_notional) / pos.size
        pos.updated_at = utc_now()

    def _remove_from_position(self, order: Order, price: float, size: float) -> float:
        iid = order.instrument_id or order.token_id
        pos = self._positions.get(iid)
        if pos is None:
            logger.warning("sell_without_position", instrument_id=iid)
            return 0.0

        realized = (price - pos.avg_entry_price) * min(size, pos.size)
        pos.size = max(0.0, pos.size - size)
        pos.realized_pnl += realized
        pos.updated_at = utc_now()

        if pos.size <= 0:
            del self._positions[iid]

        return realized

    def mark_to_market(self, instrument_id: str, mark_price: float) -> None:
        with self._lock:
            pos = self._positions.get(instrument_id)
            if pos is None:
                return
            pos.last_mark_price = mark_price
            pos.unrealized_pnl = (mark_price - pos.avg_entry_price) * pos.size
            pos.updated_at = utc_now()

    def start_new_day(self) -> None:
        with self._lock:
            equity = self._cash + sum(p.market_value for p in self._positions.values())
            self._daily_start_equity = equity
            self._daily_realized_pnl = 0.0
        logger.info("new_day_started", equity=equity)

    def restore_position(
        self,
        token_id: str = "",
        market_id: str = "",
        token_side: OutcomeSide = OutcomeSide.YES,
        size: float = 0.0,
        avg_entry_price: float = 0.0,
        realized_pnl: float = 0.0,
        *,
        instrument_id: str = "",
        exchange: str = "",
    ) -> None:
        """Restore a single position from persistent storage on startup."""
        iid = instrument_id or token_id
        with self._lock:
            pos = Position(
                market_id=market_id,
                token_id=iid,
                instrument_id=iid,
                exchange=exchange,
                token_side=token_side,
                size=size,
                avg_entry_price=avg_entry_price,
                realized_pnl=realized_pnl,
            )
            self._positions[iid] = pos
            self._realized_pnl += realized_pnl
        logger.info(
            "position_restored",
            instrument_id=iid,
            exchange=exchange,
            size=size,
            avg_entry=avg_entry_price,
        )

    def export_summary(self, path: Path | None = None) -> dict:
        snap = self.get_snapshot()
        summary = {
            "timestamp": snap.timestamp.isoformat(),
            "cash": snap.cash,
            "total_exposure": snap.total_exposure,
            "total_unrealized_pnl": snap.total_unrealized_pnl,
            "total_realized_pnl": snap.total_realized_pnl,
            "daily_pnl": snap.daily_pnl,
            "position_count": len(snap.positions),
            "positions": [p.model_dump() for p in snap.positions],
        }
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(summary, f, indent=2, default=str)
            logger.info("portfolio_summary_exported", path=str(path))
        return summary
