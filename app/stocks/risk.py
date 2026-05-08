"""Stock-specific risk manager.

This is the single source of truth for deterministic safety controls on the
stock side of the bot. The decision engine and the execution engine MUST
both consult ``StockRiskManager`` before any order can leave the process —
the AI/LLM/ML layers cannot bypass these checks.

Beginner-safe defaults are applied (small position sizes, tight daily loss,
small position count, mandatory stop loss) and are loaded from
``app.config.settings.Settings``.

Checks performed (in order):
    1. Emergency stop file present
    2. Circuit breaker tripped
    3. Daily loss limit exceeded
    4. Ticker outside the approved universe
    5. Stale market data
    6. Missing stop loss when ``REQUIRE_STOP_LOSS=true``
    7. Order notional > max position dollars
    8. Portfolio exposure + order > max portfolio dollars
    9. Open positions >= max open positions (BUY only)
   10. Trades-per-day limit reached
   11. Order frequency (per-minute) limit
   12. Market closed (when ``ALLOW_EXTENDED_HOURS=false``)
   13. Insufficient cash
   14. Revenge-trade guard (consecutive losses on the same symbol)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from app.brokers.base import BaseBrokerAdapter
from app.config.settings import Settings
from app.data.models import PortfolioSnapshot
from app.monitoring import get_logger

logger = get_logger(__name__)


@dataclass
class StockRiskCheckResult:
    approved: bool
    reason: str = ""
    checks: list[str] = field(default_factory=list)


class StockRiskManager:
    """Deterministic risk gate for stock orders.

    The constructor is permissive about which fields are set on the
    ``Settings`` object so existing tests that build a minimal Settings
    keep working.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._max_position_dollars = settings.stock_max_position_dollars
        self._max_portfolio_dollars = settings.stock_max_portfolio_dollars
        self._max_daily_loss = settings.stock_max_daily_loss_dollars
        self._max_open_positions = settings.stock_max_open_positions
        self._max_orders_per_minute = settings.stock_max_orders_per_minute
        self._max_trades_per_day = getattr(settings, "stock_max_trades_per_day", 5)
        self._require_stop_loss = getattr(settings, "stock_require_stop_loss", True)
        self._max_consec_losses_per_symbol = getattr(
            settings, "stock_max_consecutive_losses_per_symbol", 2
        )
        self._max_bar_age_seconds = getattr(settings, "stock_max_bar_age_seconds", 120)
        self._allow_extended_hours = settings.allow_extended_hours
        self._emergency_stop_file = settings.emergency_stop_file
        self._approved_tickers = settings.approved_ticker_set

        self._circuit_breaker_tripped = False
        self._halt_reason = ""
        self._daily_pnl = 0.0
        self._consecutive_losses = 0
        self._consecutive_losses_by_symbol: dict[str, int] = {}
        self._order_timestamps: deque[float] = deque(maxlen=240)
        self._trades_today: int = 0
        self._trade_day: date = datetime.now(timezone.utc).date()
        self._lock = threading.Lock()
        self._cancel_all_callback = None

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        return self._circuit_breaker_tripped or self._emergency_stop_file.exists()

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def trades_today(self) -> int:
        return self._trades_today

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    def set_cancel_all_callback(self, cb) -> None:
        self._cancel_all_callback = cb

    # ── Pre-trade gate ─────────────────────────────────────────────────

    def check_order(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        portfolio: PortfolioSnapshot,
        *,
        broker: BaseBrokerAdapter | None = None,
        stop_price: float | None = None,
        bar_timestamp: datetime | None = None,
    ) -> StockRiskCheckResult:
        """Run all deterministic safety checks for a single order.

        Returns ``StockRiskCheckResult(approved=True)`` only when every
        gate passes. ``checks`` lists the gates evaluated, useful for
        the decision trace.
        """
        checks: list[str] = []
        symbol_upper = symbol.upper()

        with self._lock:
            self._roll_day_if_needed()

            checks.append("emergency_stop")
            if self._emergency_stop_file.exists():
                return self._deny("Emergency stop file exists", checks)

            checks.append("circuit_breaker")
            if self._circuit_breaker_tripped:
                return self._deny(f"Circuit breaker: {self._halt_reason}", checks)

            checks.append("daily_loss_limit")
            if self._daily_pnl < -self._max_daily_loss:
                self.trip_circuit_breaker(
                    f"Daily loss {self._daily_pnl:.2f} exceeds limit"
                )
                return self._deny("Daily loss limit exceeded", checks)

            checks.append("ticker_universe")
            if self._approved_tickers and symbol_upper not in self._approved_tickers:
                return self._deny(
                    f"Ticker {symbol_upper} not in approved universe",
                    checks,
                )

            checks.append("data_freshness")
            if bar_timestamp is not None:
                age = (datetime.now(timezone.utc) - bar_timestamp).total_seconds()
                if age > self._max_bar_age_seconds:
                    return self._deny(
                        f"Stale market data ({age:.0f}s > "
                        f"{self._max_bar_age_seconds}s)",
                        checks,
                    )

            checks.append("stop_loss_required")
            if (
                side.upper() == "BUY"
                and self._require_stop_loss
                and stop_price is None
            ):
                return self._deny(
                    "Stop loss required by REQUIRE_STOP_LOSS=true",
                    checks,
                )

            checks.append("max_position_size")
            order_value = price * quantity
            if order_value > self._max_position_dollars:
                return self._deny(
                    f"Order ${order_value:.2f} exceeds max position "
                    f"${self._max_position_dollars:.2f}",
                    checks,
                )

            checks.append("max_portfolio_exposure")
            if portfolio.total_exposure + order_value > self._max_portfolio_dollars:
                return self._deny(
                    "Would exceed max portfolio exposure",
                    checks,
                )

            checks.append("max_open_positions")
            position_count = (
                len(portfolio.positions) if hasattr(portfolio, "positions") else 0
            )
            if (
                position_count >= self._max_open_positions
                and side.upper() == "BUY"
            ):
                return self._deny(
                    f"Max open positions ({self._max_open_positions}) reached",
                    checks,
                )

            checks.append("max_trades_per_day")
            if (
                side.upper() == "BUY"
                and self._trades_today >= self._max_trades_per_day
            ):
                return self._deny(
                    f"Max trades per day ({self._max_trades_per_day}) reached",
                    checks,
                )

            checks.append("order_frequency")
            now = time.time()
            self._order_timestamps.append(now)
            recent = [t for t in self._order_timestamps if now - t < 60]
            if len(recent) > self._max_orders_per_minute:
                return self._deny("Order frequency limit exceeded", checks)

            checks.append("market_hours")
            if (
                broker is not None
                and not self._allow_extended_hours
                and not broker.is_market_open()
            ):
                return self._deny(
                    "Market is closed and extended hours disabled",
                    checks,
                )

            checks.append("sufficient_cash")
            if side.upper() == "BUY" and price * quantity > portfolio.cash:
                return self._deny("Insufficient cash", checks)

            checks.append("revenge_trade_guard")
            losses = self._consecutive_losses_by_symbol.get(symbol_upper, 0)
            if (
                side.upper() == "BUY"
                and losses >= self._max_consec_losses_per_symbol
            ):
                return self._deny(
                    f"Revenge-trade guard active for {symbol_upper}: "
                    f"{losses} consecutive losses",
                    checks,
                )

            if side.upper() == "BUY":
                self._trades_today += 1

            return StockRiskCheckResult(approved=True, checks=checks)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def record_fill(self, pnl: float, *, symbol: str | None = None) -> None:
        with self._lock:
            self._daily_pnl += pnl
            if pnl < 0:
                self._consecutive_losses += 1
                if symbol:
                    sym = symbol.upper()
                    self._consecutive_losses_by_symbol[sym] = (
                        self._consecutive_losses_by_symbol.get(sym, 0) + 1
                    )
            else:
                self._consecutive_losses = 0
                if symbol:
                    self._consecutive_losses_by_symbol.pop(symbol.upper(), None)

    def trip_circuit_breaker(self, reason: str) -> None:
        self._circuit_breaker_tripped = True
        self._halt_reason = reason
        logger.error("stock_circuit_breaker_tripped", reason=reason)
        if self._cancel_all_callback:
            try:
                import asyncio
                asyncio.create_task(self._cancel_all_callback())
            except RuntimeError:
                pass

    def reset_circuit_breaker(self) -> None:
        self._circuit_breaker_tripped = False
        self._halt_reason = ""
        logger.info("stock_circuit_breaker_reset")

    def reset_daily_counters(self) -> None:
        with self._lock:
            self._daily_pnl = 0.0
            self._consecutive_losses = 0
            self._trades_today = 0
            self._consecutive_losses_by_symbol.clear()

    # ── Internal ───────────────────────────────────────────────────────

    def _roll_day_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._trade_day:
            self._trade_day = today
            self._trades_today = 0
            self._daily_pnl = 0.0

    def _deny(self, reason: str, checks: list[str]) -> StockRiskCheckResult:
        logger.info("stock_order_blocked", reason=reason)
        return StockRiskCheckResult(approved=False, reason=reason, checks=checks)

    def status_dict(self) -> dict:
        """Snapshot of the current risk state for logging / API responses."""
        return {
            "halted": self.is_halted,
            "circuit_breaker_tripped": self._circuit_breaker_tripped,
            "halt_reason": self._halt_reason,
            "daily_pnl": self._daily_pnl,
            "max_daily_loss": self._max_daily_loss,
            "trades_today": self._trades_today,
            "max_trades_per_day": self._max_trades_per_day,
            "consecutive_losses": self._consecutive_losses,
            "open_positions_limit": self._max_open_positions,
            "approved_universe": sorted(self._approved_tickers),
            "emergency_stop_file_exists": self._emergency_stop_file.exists(),
        }
