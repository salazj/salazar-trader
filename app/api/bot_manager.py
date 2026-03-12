"""
BotManager — manages the TradingBot lifecycle from the API layer.

All GUI/API access to bot internals flows through this class.
Route handlers never reach into TradingBot directly.
"""

from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from typing import Any

from app.api.schemas import (
    BotStatusResponse,
    FillItem,
    OrderItem,
    PnLHistoryItem,
    PortfolioResponse,
    PositionItem,
    RiskStateResponse,
    RunConfig,
    ValidationResult,
)
from app.config.settings import Settings, get_settings
from app.monitoring import get_logger

logger = get_logger(__name__)


class BotManager:
    """Manages the trading bot lifecycle from the API layer.

    Provides a clean boundary between the control API and bot internals.
    All data retrieval is encapsulated here so route handlers never touch
    private bot attributes directly.
    """

    def __init__(self) -> None:
        self._bot: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._config: RunConfig | None = None
        self._started_at: float | None = None
        self._error: str | None = None
        self._session_id: str = ""
        self._lock = asyncio.Lock()

    # ── Status ─────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def session_id(self) -> str:
        return self._session_id

    def get_status(self) -> BotStatusResponse:
        uptime = 0.0
        if self._started_at and self.is_running:
            uptime = time.time() - self._started_at

        cfg = self._config
        return BotStatusResponse(
            running=self.is_running,
            status="running" if self.is_running else ("error" if self._error else "stopped"),
            session_id=self._session_id,
            asset_class=cfg.asset_class if cfg else "",
            exchange=cfg.exchange if cfg else "",
            broker=cfg.broker if cfg else "",
            mode="live" if (cfg and not cfg.dry_run) else "dry-run",
            dry_run=cfg.dry_run if cfg else True,
            live_trading=bool(
                cfg
                and not cfg.dry_run
                and cfg.enable_live_trading
                and cfg.live_trading_acknowledged
            ),
            uptime_seconds=round(uptime, 1),
            error=self._error,
            started_at=(
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._started_at))
                if self._started_at
                else None
            ),
        )

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self, config: RunConfig) -> BotStatusResponse:
        async with self._lock:
            if self.is_running:
                raise RuntimeError("Bot is already running. Stop it first.")

            validation = self._validate_config(config)
            if validation.errors:
                raise ValueError("; ".join(validation.errors))

            self._error = None
            self._config = config
            self._session_id = uuid.uuid4().hex[:12]
            settings = self._build_settings(config)

            from app.main import TradingBot

            self._bot = TradingBot(settings)
            self._started_at = time.time()
            self._task = asyncio.create_task(self._run_bot(config))
            logger.info(
                "bot_session_started",
                session_id=self._session_id,
                asset_class=config.asset_class,
            )
            return self.get_status()

    async def stop(self) -> BotStatusResponse:
        async with self._lock:
            old_session = self._session_id
            if self._bot is not None:
                try:
                    await self._bot.stop()
                except Exception as exc:
                    logger.error("bot_stop_error", error=str(exc))
            if self._task is not None and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
            self._bot = None
            self._task = None
            self._started_at = None
            self._session_id = ""
            self._error = None
            logger.info("bot_session_stopped", session_id=old_session)
            return self.get_status()

    async def restart(self, config: RunConfig | None = None) -> BotStatusResponse:
        await self.stop()
        return await self.start(config or self._config or RunConfig())

    # ── Portfolio / Risk accessors ─────────────────────────────────────

    def get_portfolio(self) -> PortfolioResponse:
        if self._bot is None:
            return PortfolioResponse()
        try:
            snap = self._bot._portfolio.get_snapshot()
            positions = [
                PositionItem(
                    instrument_id=or_default(p, "instrument_id", "token_id"),
                    symbol=or_default(p, "instrument_id", "token_id"),
                    exchange=getattr(p, "exchange", ""),
                    side=p.token_side.value if hasattr(p.token_side, "value") else str(p.token_side),
                    size=p.size,
                    avg_entry_price=p.avg_entry_price,
                    mark_price=p.last_mark_price,
                    unrealized_pnl=p.unrealized_pnl,
                    realized_pnl=p.realized_pnl,
                )
                for p in snap.positions
            ]
            return PortfolioResponse(
                cash=snap.cash,
                total_exposure=snap.total_exposure,
                total_unrealized_pnl=snap.total_unrealized_pnl,
                total_realized_pnl=snap.total_realized_pnl,
                daily_pnl=snap.daily_pnl,
                position_count=len(positions),
                positions=positions,
            )
        except Exception:
            return PortfolioResponse()

    def get_orders(self, limit: int = 50) -> list[OrderItem]:
        if self._bot is None:
            return []
        try:
            orders = list(self._bot._execution.all_orders)[-limit:]
            return [
                OrderItem(
                    order_id=o.order_id,
                    instrument_id=or_default(o, "instrument_id", "token_id"),
                    exchange=getattr(o, "exchange", ""),
                    side=o.side.value,
                    price=o.price,
                    size=o.size,
                    filled_size=o.filled_size,
                    status=o.status.value,
                    created_at=o.created_at.isoformat(),
                )
                for o in orders
            ]
        except Exception:
            return []

    async def get_fills(self, limit: int = 50) -> list[FillItem]:
        """Retrieve recent fills via the repository. Encapsulates DB access."""
        if self._bot is None:
            return []
        try:
            repo = self._bot._repository
            if repo._db is None:
                return []
            cursor = await repo._db.execute(
                "SELECT order_id, price, size, pnl, filled_at "
                "FROM fills ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                FillItem(
                    order_id=r[0],
                    price=r[1],
                    size=r[2],
                    pnl=r[3],
                    filled_at=r[4],
                )
                for r in rows
            ]
        except Exception:
            return []

    async def get_pnl_history(self, limit: int = 200) -> list[PnLHistoryItem]:
        """Retrieve PnL history via the repository. Encapsulates DB access."""
        if self._bot is None:
            return []
        try:
            rows = await self._bot._repository.get_pnl_history(limit=limit)
            return [
                PnLHistoryItem(
                    timestamp=r.get("timestamp", ""),
                    cash=r.get("cash", 0.0),
                    total_exposure=r.get("total_exposure", 0.0),
                    unrealized_pnl=r.get("total_unrealized_pnl", 0.0),
                    realized_pnl=r.get("total_realized_pnl", 0.0),
                    daily_pnl=r.get("daily_pnl", 0.0),
                )
                for r in rows
            ]
        except Exception:
            return []

    def get_risk_state(self) -> RiskStateResponse:
        if self._bot is None:
            cfg = self._config
            return RiskStateResponse(
                max_daily_loss=cfg.max_daily_loss if cfg else 10.0,
            )
        try:
            rm = self._bot._risk_manager
            return RiskStateResponse(
                halted=rm.is_halted,
                halt_reason=getattr(rm, "_halt_reason", ""),
                circuit_breaker_tripped=getattr(rm, "_circuit_breaker_tripped", False),
                daily_loss=getattr(rm, "_daily_pnl", 0.0),
                max_daily_loss=self._bot._settings.max_daily_loss,
                consecutive_losses=getattr(rm, "_consecutive_losses", 0),
                orders_this_minute=getattr(rm, "_orders_this_minute", 0),
                emergency_stop_file_exists=self._bot._settings.emergency_stop_file.exists(),
            )
        except Exception:
            return RiskStateResponse()

    def reset_circuit_breaker(self) -> None:
        """Reset the circuit breaker via the risk manager."""
        if self._bot is None:
            raise RuntimeError("Bot is not running")
        self._bot._risk_manager.reset_circuit_breaker()

    def trip_emergency_stop(self, reason: str = "Emergency stop via GUI") -> None:
        """Trip the circuit breaker for an emergency stop."""
        if self._bot is None:
            raise RuntimeError("Bot is not running")
        self._bot._risk_manager.trip_circuit_breaker(reason)

    # ── Config ─────────────────────────────────────────────────────────

    def get_run_config(self) -> RunConfig | None:
        return self._config

    def validate_config(self, config: RunConfig) -> ValidationResult:
        return self._validate_config(config)

    # ── Internal ───────────────────────────────────────────────────────

    async def _run_bot(self, config: RunConfig) -> None:
        try:
            slugs = config.market_slugs if config.market_slugs else None
            await self._bot.start(market_slugs=slugs)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._error = str(exc)
            logger.error(
                "bot_crashed",
                session_id=self._session_id,
                error=self._error,
                tb=traceback.format_exc(),
            )
        finally:
            self._started_at = None
            if not self._error:
                self._error = "Bot stopped unexpectedly. Check the logs for details."

    def _validate_config(self, config: RunConfig) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if config.asset_class not in ("prediction_markets", "equities"):
            errors.append(f"Invalid asset_class: {config.asset_class}")

        if config.asset_class == "prediction_markets":
            if config.exchange not in ("polymarket", "kalshi"):
                errors.append(f"Invalid exchange: {config.exchange}")
        elif config.asset_class == "equities":
            if config.broker not in ("alpaca",):
                errors.append(f"Invalid broker: {config.broker}")

        if config.decision_mode not in ("conservative", "balanced", "aggressive"):
            errors.append(f"Invalid decision_mode: {config.decision_mode}")

        weight_sum = config.ensemble_weight_l1 + config.ensemble_weight_l2 + config.ensemble_weight_l3
        if abs(weight_sum - 1.0) > 0.05:
            warnings.append(
                f"Ensemble weights sum to {weight_sum:.2f} (expected ~1.0). "
                "Signals will be renormalized internally."
            )

        if config.max_daily_loss <= 0:
            errors.append("max_daily_loss must be positive")
        if config.max_total_exposure <= 0:
            errors.append("max_total_exposure must be positive")
        if config.stock_max_daily_loss_dollars <= 0:
            errors.append("stock_max_daily_loss_dollars must be positive")

        if not config.dry_run:
            if not config.enable_live_trading:
                errors.append("Live trading requires enable_live_trading=true")
            if not config.live_trading_acknowledged:
                errors.append("Live trading requires live_trading_acknowledged=true")
            env_settings = get_settings()
            if config.asset_class == "equities":
                if not env_settings.has_alpaca_credentials:
                    errors.append("Missing Alpaca API credentials in .env")
            elif config.exchange == "kalshi":
                if not env_settings.has_kalshi_credentials:
                    errors.append("Missing Kalshi credentials in .env")
            else:
                if not env_settings.has_polymarket_credentials:
                    errors.append("Missing Polymarket credentials in .env")
            warnings.append("LIVE TRADING IS ENABLED — real orders will be submitted")

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    def _build_settings(self, config: RunConfig) -> Settings:
        """Merge GUI config with environment defaults to create a Settings object."""
        env_settings = get_settings()
        overrides: dict[str, Any] = {
            "asset_class": config.asset_class,
            "exchange": config.exchange,
            "broker": config.broker,
            "dry_run": config.dry_run,
            "enable_live_trading": config.enable_live_trading,
            "live_trading_acknowledged": config.live_trading_acknowledged,
            "decision_mode": config.decision_mode,
            "ensemble_weight_l1": config.ensemble_weight_l1,
            "ensemble_weight_l2": config.ensemble_weight_l2,
            "ensemble_weight_l3": config.ensemble_weight_l3,
            "min_ensemble_confidence": config.min_ensemble_confidence,
            "min_layers_agree": config.min_layers_agree,
            "min_evidence_signals": config.min_evidence_signals,
            "nlp_provider": config.nlp_provider,
            "llm_provider": config.llm_provider,
            "max_tracked_markets": config.max_tracked_markets,
            "max_subscribed_markets": config.max_subscribed_markets,
            "include_categories": config.include_categories,
            "exclude_categories": config.exclude_categories,
            "stock_universe_mode": config.stock_universe_mode,
            "stock_tickers": config.stock_tickers,
            "stock_min_volume": config.stock_min_volume,
            "stock_min_price": config.stock_min_price,
            "stock_max_price": config.stock_max_price,
            "stock_sector_include": config.stock_sector_include,
            "allow_extended_hours": config.allow_extended_hours,
            "max_position_per_market": config.max_position_per_market,
            "max_total_exposure": config.max_total_exposure,
            "max_daily_loss": config.max_daily_loss,
            "stock_max_position_dollars": config.stock_max_position_dollars,
            "stock_max_portfolio_dollars": config.stock_max_portfolio_dollars,
            "stock_max_daily_loss_dollars": config.stock_max_daily_loss_dollars,
            "stock_max_open_positions": config.stock_max_open_positions,
        }

        merged = {}
        for field_name in Settings.model_fields:
            if field_name in overrides:
                merged[field_name] = overrides[field_name]
            else:
                merged[field_name] = getattr(env_settings, field_name)

        return Settings(**merged)


def or_default(obj: Any, primary: str, fallback: str) -> str:
    """Get primary attribute, falling back to fallback if empty."""
    return getattr(obj, primary, "") or getattr(obj, fallback, "")
