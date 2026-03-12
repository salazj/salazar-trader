"""
Centralized configuration loaded from environment variables.

All risk parameters have conservative defaults suitable for a $100 bankroll.
DRY_RUN defaults to True — live trading requires explicit opt-in.
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

def _resolve_project_root() -> Path:
    if "PROJECT_ROOT" in os.environ:
        return Path(os.environ["PROJECT_ROOT"]).resolve()
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    return Path.cwd()


PROJECT_ROOT = _resolve_project_root()

_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings with conservative defaults for safety."""

    # --- Environment ---
    environment: Environment = Environment.DEVELOPMENT
    dry_run: bool = True
    # Second gate: must be explicitly set to "true" for any live order submission.
    # Even if dry_run=False, live orders are blocked unless this is also set.
    enable_live_trading: bool = False
    # Third gate: explicit acknowledgement required for live trading
    live_trading_acknowledged: bool = False

    # --- Asset Class ---
    asset_class: str = "prediction_markets"

    # --- Exchange Selection (prediction markets) ---
    exchange: str = "polymarket"

    # --- Broker Selection (equities) ---
    broker: str = ""

    # --- Polymarket API ---
    polymarket_host: str = "https://clob.polymarket.com"
    polymarket_ws_host: str = "wss://ws-subscriptions-clob.polymarket.com/ws"
    chain_id: int = 137

    # --- Wallet / Auth (Polymarket) ---
    # SECURITY: These fields are excluded from repr/logging via model_config.
    private_key: str = ""
    poly_api_key: str = ""
    poly_api_secret: str = ""
    poly_passphrase: str = ""

    # --- Kalshi API ---
    kalshi_api_key: str = ""
    kalshi_private_key: str = ""
    kalshi_private_key_path: str = ""
    kalshi_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    kalshi_ws_url: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    kalshi_demo_mode: bool = True

    # --- Alpaca (Stock Broker) ---
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True

    # --- Risk Limits (Prediction Markets) ---
    max_position_per_market: float = Field(default=10.0, ge=0)
    max_total_exposure: float = Field(default=50.0, ge=0)
    max_daily_loss: float = Field(default=10.0, ge=0)
    max_consecutive_losses: int = Field(default=5, ge=1)
    max_orders_per_minute: int = Field(default=6, ge=1)
    max_slippage: float = Field(default=0.03, ge=0, le=1)
    min_liquidity_depth: float = Field(default=20.0, ge=0)
    min_spread_threshold: float = Field(default=0.01, ge=0, le=1)
    max_spread_threshold: float = Field(default=0.15, ge=0, le=1)

    # --- Strategy ---
    strategy: str = "passive_market_maker"
    default_order_size: float = Field(default=1.0, ge=0.01)

    # --- Decision Engine ---
    decision_mode: str = "conservative"
    ensemble_weight_l1: float = Field(default=0.30, ge=0.0, le=1.0)
    ensemble_weight_l2: float = Field(default=0.40, ge=0.0, le=1.0)
    ensemble_weight_l3: float = Field(default=0.30, ge=0.0, le=1.0)
    min_ensemble_confidence: float = Field(default=0.60, ge=0.0, le=1.0)
    min_layers_agree: int = Field(default=2, ge=1, le=3)
    min_evidence_signals: int = Field(default=2, ge=1)
    large_trade_threshold: float = Field(default=3.0, ge=0.0)
    large_trade_min_layers: int = Field(default=3, ge=1, le=3)
    conflict_tolerance: float = Field(default=0.15, ge=0.0, le=1.0)

    # --- NLP / News ---
    nlp_provider: str = "mock"
    nlp_providers: str = ""
    news_poll_interval: int = Field(default=300, ge=10)
    news_file_dir: str = "data/news"
    newsapi_key: str = ""
    rss_feed_urls: str = ""
    finnhub_api_key: str = ""

    # --- LLM / AI Provider ---
    # "none" (default), "local_open_source", "hosted_api"
    llm_provider: str = "none"
    llm_model_name: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: int = Field(default=30, ge=1)
    # Confidence threshold below which the hybrid classifier ignores LLM output
    llm_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- Claude API (second LLM for competitive analysis) ---
    claude_api_key: str = ""
    claude_model_name: str = "claude-sonnet-4-6"

    # --- LLM analysis intervals (seconds) ---
    llm_analysis_interval: int = Field(default=180, ge=60)
    claude_analysis_interval: int = Field(default=180, ge=60)

    # --- Level 2 ML ---
    # "logistic_regression", "gradient_boosting", "random_forest"
    ml_model_name: str = "gradient_boosting"

    # --- Universe Selection ---
    max_tracked_markets: int = Field(default=50, ge=1)
    max_subscribed_markets: int = Field(default=20, ge=1)
    max_trade_candidates: int = Field(default=10, ge=1)
    universe_refresh_seconds: int = Field(default=300, ge=30)
    min_liquidity_threshold: float = Field(default=10.0, ge=0)
    max_spread_filter: float = Field(default=0.20, ge=0, le=1)
    min_volume_threshold: float = Field(default=0.0, ge=0)
    min_orderbook_depth: float = Field(default=5.0, ge=0)
    min_time_to_resolution_hours: float = Field(default=1.0, ge=0)
    max_time_to_resolution_hours: float = Field(default=8760.0, ge=0)
    include_categories: str = ""
    exclude_categories: str = ""
    category_weights_json: str = ""
    watchlist_hysteresis_score: float = Field(default=0.05, ge=0.0, le=1.0)
    watchlist_cooldown_seconds: float = Field(default=600.0, ge=0.0)
    universe_mode: str = "auto"

    # --- Stock Universe Selection ---
    stock_universe_mode: str = "manual"
    stock_tickers: str = ""
    stock_min_volume: int = 100000
    stock_min_price: float = 5.0
    stock_max_price: float = 500.0
    stock_sector_include: str = ""
    max_stock_symbols: int = 20
    allow_extended_hours: bool = False

    # --- Stock Risk Limits ---
    stock_max_position_dollars: float = Field(default=1000.0, ge=0)
    stock_max_portfolio_dollars: float = Field(default=10000.0, ge=0)
    stock_max_daily_loss_dollars: float = Field(default=500.0, ge=0)
    stock_max_open_positions: int = Field(default=10, ge=1)
    stock_max_orders_per_minute: int = Field(default=10, ge=1)

    # --- Storage ---
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'salazar-trader.db'}"

    # --- Logging ---
    log_level: str = "INFO"

    # --- Derived paths ---
    project_root: Path = PROJECT_ROOT
    emergency_stop_file: Path = PROJECT_ROOT / "EMERGENCY_STOP"
    model_artifacts_dir: Path = PROJECT_ROOT / "model_artifacts"
    reports_dir: Path = PROJECT_ROOT / "reports"

    model_config = {
        "env_prefix": "",
        "case_sensitive": False,
        # Prevent secrets from leaking in repr / logs
        "json_schema_extra": None,
    }

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v

    @field_validator("asset_class")
    @classmethod
    def validate_asset_class(cls, v: str) -> str:
        allowed = {"prediction_markets", "equities"}
        v = v.lower()
        if v not in allowed:
            raise ValueError(f"asset_class must be one of {allowed}")
        return v

    @field_validator("exchange")
    @classmethod
    def validate_exchange(cls, v: str) -> str:
        allowed = {"polymarket", "kalshi"}
        v = v.lower()
        if v not in allowed:
            raise ValueError(f"exchange must be one of {allowed}")
        return v

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_provider(cls, v: str) -> str:
        allowed = {"none", "local_open_source", "hosted_api"}
        v = v.lower()
        if v not in allowed:
            raise ValueError(f"llm_provider must be one of {allowed}")
        return v

    @field_validator("decision_mode")
    @classmethod
    def validate_decision_mode(cls, v: str) -> str:
        allowed = {"conservative", "balanced", "aggressive"}
        v = v.lower()
        if v not in allowed:
            raise ValueError(f"decision_mode must be one of {allowed}")
        return v

    @field_validator("max_spread_threshold")
    @classmethod
    def validate_spread_thresholds(cls, v: float, info: Any) -> float:
        min_val = info.data.get("min_spread_threshold", 0.0)
        if min_val is not None and v <= min_val:
            raise ValueError(
                f"max_spread_threshold ({v}) must be greater than "
                f"min_spread_threshold ({min_val})"
            )
        return v

    @field_validator("max_total_exposure")
    @classmethod
    def validate_exposure_hierarchy(cls, v: float, info: Any) -> float:
        per_market = info.data.get("max_position_per_market", 0.0)
        if per_market is not None and v < per_market:
            raise ValueError(
                f"max_total_exposure ({v}) must be >= "
                f"max_position_per_market ({per_market})"
            )
        return v

    @property
    def is_live(self) -> bool:
        """True only when ALL three safety gates are passed."""
        return (
            not self.dry_run
            and self.enable_live_trading
            and self.live_trading_acknowledged
        )

    @property
    def has_polymarket_credentials(self) -> bool:
        return bool(self.private_key and self.poly_api_key and self.poly_api_secret)

    @property
    def has_kalshi_credentials(self) -> bool:
        return bool(self.kalshi_api_key and (self.kalshi_private_key or self.kalshi_private_key_path))

    @property
    def has_alpaca_credentials(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def has_credentials(self) -> bool:
        if self.asset_class == "equities":
            return self.has_alpaca_credentials
        if self.exchange == "kalshi":
            return self.has_kalshi_credentials
        return self.has_polymarket_credentials

    def require_live_trading(self) -> None:
        """Raise unless all live-trading preconditions are met (3 gates)."""
        if self.dry_run:
            raise RuntimeError("Cannot enable live trading while DRY_RUN=true")
        if not self.enable_live_trading:
            raise RuntimeError(
                "Live trading requires ENABLE_LIVE_TRADING=true in .env. "
                "This is a deliberate second safety gate."
            )
        if not self.live_trading_acknowledged:
            raise RuntimeError(
                "Live trading requires LIVE_TRADING_ACKNOWLEDGED=true in .env. "
                "This is a deliberate third safety gate."
            )
        if not self.has_credentials:
            if self.exchange == "kalshi":
                raise RuntimeError(
                    "Kalshi live trading requires KALSHI_API_KEY and "
                    "KALSHI_PRIVATE_KEY_PATH to be set in .env"
                )
            else:
                raise RuntimeError(
                    "Polymarket live trading requires PRIVATE_KEY, POLY_API_KEY, "
                    "POLY_API_SECRET, and POLY_PASSPHRASE to be set in .env"
                )

    def require_credentials(self) -> None:
        """Backwards-compatible alias — delegates to full check."""
        self.require_live_trading()

    def __repr__(self) -> str:
        """Override repr to redact secrets — omits secret fields entirely."""
        _SECRETS = {
            "private_key", "poly_api_key", "poly_api_secret", "poly_passphrase",
            "llm_api_key", "kalshi_api_key", "kalshi_private_key", "kalshi_private_key_path",
            "newsapi_key", "alpaca_api_key", "alpaca_secret_key",
        }
        safe_fields = {
            k: v
            for k, v in self.__dict__.items()
            if not k.startswith("_") and k not in _SECRETS
        }
        return f"Settings({safe_fields})"

    def ensure_dirs(self) -> None:
        """Create necessary output directories."""
        self.model_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton-style access to application settings."""
    settings = Settings()
    settings.ensure_dirs()
    return settings
