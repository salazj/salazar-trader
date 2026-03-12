"""Pydantic request/response models for the control API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── Run configuration ─────────────────────────────────────────────────


class RunConfig(BaseModel):
    """Configuration submitted by the GUI to start a bot run."""

    asset_class: str = "prediction_markets"
    exchange: str = "polymarket"
    broker: str = "alpaca"

    dry_run: bool = True
    enable_live_trading: bool = False
    live_trading_acknowledged: bool = False

    strategies: list[str] = []

    decision_mode: str = "conservative"
    ensemble_weight_l1: float = 0.30
    ensemble_weight_l2: float = 0.40
    ensemble_weight_l3: float = 0.30
    min_ensemble_confidence: float = 0.60
    min_layers_agree: int = 2
    min_evidence_signals: int = 2

    nlp_provider: str = "mock"
    llm_provider: str = "none"

    max_tracked_markets: int = 50
    max_subscribed_markets: int = 20
    include_categories: str = ""
    exclude_categories: str = ""

    stock_universe_mode: str = "manual"
    stock_tickers: str = ""
    stock_min_volume: int = 100000
    stock_min_price: float = 5.0
    stock_max_price: float = 500.0
    stock_sector_include: str = ""
    allow_extended_hours: bool = False

    max_position_per_market: float = 10.0
    max_total_exposure: float = 50.0
    max_daily_loss: float = 10.0

    stock_max_position_dollars: float = 1000.0
    stock_max_portfolio_dollars: float = 10000.0
    stock_max_daily_loss_dollars: float = 500.0
    stock_max_open_positions: int = 10

    market_slugs: list[str] = []


# ── Response models ───────────────────────────────────────────────────


class BotStatusResponse(BaseModel):
    running: bool = False
    status: str = "stopped"
    session_id: str = ""
    asset_class: str = ""
    exchange: str = ""
    broker: str = ""
    mode: str = "dry-run"
    dry_run: bool = True
    live_trading: bool = False
    uptime_seconds: float = 0.0
    error: str | None = None
    started_at: str | None = None


class PositionItem(BaseModel):
    instrument_id: str = ""
    symbol: str = ""
    exchange: str = ""
    side: str = ""
    size: float = 0.0
    avg_entry_price: float = 0.0
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


class OrderItem(BaseModel):
    order_id: str = ""
    instrument_id: str = ""
    exchange: str = ""
    side: str = ""
    price: float = 0.0
    size: float = 0.0
    filled_size: float = 0.0
    status: str = ""
    created_at: str = ""


class FillItem(BaseModel):
    order_id: str = ""
    instrument_id: str = ""
    price: float = 0.0
    size: float = 0.0
    pnl: float = 0.0
    filled_at: str = ""


class PnLHistoryItem(BaseModel):
    timestamp: str = ""
    cash: float = 0.0
    total_exposure: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    daily_pnl: float = 0.0


class PortfolioResponse(BaseModel):
    cash: float = 0.0
    total_exposure: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_realized_pnl: float = 0.0
    daily_pnl: float = 0.0
    position_count: int = 0
    positions: list[PositionItem] = []


class RiskStateResponse(BaseModel):
    halted: bool = False
    halt_reason: str = ""
    circuit_breaker_tripped: bool = False
    daily_loss: float = 0.0
    max_daily_loss: float = 0.0
    consecutive_losses: int = 0
    orders_this_minute: int = 0
    emergency_stop_file_exists: bool = False


class ExchangeInfo(BaseModel):
    id: str
    name: str
    asset_class: str
    config_fields: list[str] = []


class StrategyInfo(BaseModel):
    name: str
    description: str = ""
    asset_class: str = "prediction_markets"
    configurable: bool = False


class ConfigPreset(BaseModel):
    name: str
    config: RunConfig
    created_at: str = ""


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = []
    warnings: list[str] = []


class LogEntryResponse(BaseModel):
    timestamp: str = ""
    level: str = ""
    event: str = ""
    logger: str = ""
    data: dict[str, Any] = {}


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "3.0.0"
    bot_running: bool = False
    session_id: str = ""
    asset_class: str = ""
    mode: str = "dry-run"
    uptime_seconds: float = 0.0
    log_subscribers: int = 0
    timestamp: str = ""


class ServiceConfigUpdate(BaseModel):
    name: str
    enabled: bool | None = None
    interval_seconds: int | None = None


class ServiceStatsItem(BaseModel):
    name: str = ""
    label: str = ""
    type: str = ""
    status: str = "not_configured"
    enabled: bool = False
    api_calls: int = 0
    errors: int = 0
    estimated_cost: float = 0.0
    last_call_at: str | None = None
    interval_seconds: int | None = None
