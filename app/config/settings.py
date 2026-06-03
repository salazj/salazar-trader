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

    # --- Alpaca (Stock Broker) ---
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    # Market-data feed: "iex" (free) or "sip" (paid). Free/paper accounts only
    # have IEX access; requesting SIP returns empty bars.
    alpaca_data_feed: str = "iex"

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

    # --- Stop-Loss ---
    stop_loss_enabled: bool = True
    stop_loss_pct: float = Field(default=0.50, ge=0.0, le=1.0)
    stop_loss_check_interval: int = Field(default=30, ge=10)

    # --- LLM Cost Control ---
    llm_min_bet_size: float = Field(default=3.0, ge=0.0)
    llm_cost_multiplier: float = Field(default=10.0, ge=1.0)
    llm_cache_ttl: int = Field(default=600, ge=60)
    llm_cache_price_threshold: float = Field(default=0.03, ge=0.001, le=0.20)

    # --- Strategy ---
    strategy: str = "passive_market_maker"
    default_order_size: float = Field(default=1.0, ge=0.01)

    # --- Decision Engine ---
    decision_mode: str = "conservative"
    ensemble_weight_l1: float = Field(default=0.30, ge=0.0, le=1.0)
    ensemble_weight_l2: float = Field(default=0.40, ge=0.0, le=1.0)
    ensemble_weight_l3: float = Field(default=0.30, ge=0.0, le=1.0)
    min_ensemble_confidence: float = Field(default=0.30, ge=0.0, le=1.0)
    min_layers_agree: int = Field(default=1, ge=1, le=3)
    min_evidence_signals: int = Field(default=1, ge=1)
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

    # --- Sports Data (BetStack API) ---
    betstack_api_key: str = ""
    sports_leagues: str = "americanfootball_nfl,basketball_nba,baseball_mlb,icehockey_nhl"

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
    # Modes:
    #   manual           — fixed list from STOCK_TICKERS
    #   auto             — Alpaca catalog scan (legacy, weak filters)
    #   news_driven      — STOCK_TICKERS core + hot tickers from news
    #   top_movers       — STOCK_TICKERS core + Alpaca top movers
    #   news_plus_movers — STOCK_TICKERS core + hot tickers + top movers
    stock_universe_mode: str = "manual"
    stock_tickers: str = ""
    # Approved ticker allow-list — risk manager refuses anything outside this set.
    # Defaults to highly-liquid large-caps suitable for Alpaca paper trading.
    approved_stock_tickers: str = "SPY,QQQ,AAPL,MSFT,NVDA,TSLA,AMD,META,AMZN,GOOGL"
    stock_min_volume: int = 100000
    stock_min_price: float = 5.0
    stock_max_price: float = 500.0
    stock_sector_include: str = ""
    max_stock_symbols: int = 20
    allow_extended_hours: bool = False

    # --- Dynamic Universe (news_driven / top_movers modes) ---
    # Comma-separated candidate pool for the top-movers screener.
    # Empty = use the built-in S&P 100 + liquid-ETF list.
    stock_universe_candidates: str = ""
    # How often (seconds) to re-run the dynamic universe selection.
    stock_universe_refresh_seconds: int = Field(default=900, ge=60)
    # Hot-ticker tracker parameters.
    stock_hot_ticker_ttl_minutes: int = Field(default=120, ge=5)
    stock_max_hot_tickers: int = Field(default=10, ge=0)
    stock_hot_min_confidence: float = Field(default=0.4, ge=0.0, le=1.0)
    stock_hot_min_relevance: float = Field(default=0.3, ge=0.0, le=1.0)
    # Top-movers screener parameters.
    stock_movers_top_n: int = Field(default=10, ge=0)
    stock_movers_min_dollar_volume: float = Field(default=10_000_000.0, ge=0.0)

    # --- Stock Risk Limits (beginner-safe Jetson defaults) ---
    # NOTE: defaults are deliberately tiny ($50/position, $250 portfolio,
    # $25 daily loss, 3 open positions, 5 trades/day). Override via env for
    # larger accounts.
    stock_max_position_dollars: float = Field(default=50.0, ge=0)
    stock_max_portfolio_dollars: float = Field(default=250.0, ge=0)
    stock_max_daily_loss_dollars: float = Field(default=25.0, ge=0)
    stock_max_open_positions: int = Field(default=3, ge=1)
    # Risk-based position sizing: dollars risked per trade (entry→stop distance).
    # When > 0 and a stop is known, quantity = risk_dollars / per-share-risk,
    # capped by max position notional and available cash. 0 = fixed-notional.
    stock_risk_per_trade_dollars: float = Field(default=10.0, ge=0)
    # Liquidity gate for the movers screener: reject names whose bid/ask spread
    # exceeds this many basis points (illiquid → bad fills). 0 = disabled.
    stock_max_spread_bps: float = Field(default=50.0, ge=0)
    stock_max_orders_per_minute: int = Field(default=3, ge=1)
    stock_max_trades_per_day: int = Field(default=5, ge=1)
    # Per-symbol cooldown (seconds) after submitting an order, to stop the
    # intelligence loop from firing duplicate orders on a persistent signal.
    stock_order_cooldown_seconds: float = Field(default=300.0, ge=0)
    stock_require_stop_loss: bool = True
    # --- Position management (live exits beyond the broker bracket) ---
    # Flatten all positions this many minutes before the regular close (0=off).
    stock_eod_flatten_minutes: float = Field(default=10.0, ge=0)
    # Time-stop: close a position open longer than this many minutes (0=off).
    stock_max_holding_minutes: float = Field(default=240.0, ge=0)
    # Bot-side trailing stop: once in profit, exit if price falls this fraction
    # from its peak since entry (0=off; complements the broker stop).
    stock_trailing_stop_pct: float = Field(default=0.0, ge=0, le=1)
    # Self-tuning: pause a strategy that is net-losing after a minimum sample of
    # closed trades (re-enabled on the next UTC day / restart).
    stock_strategy_auto_disable: bool = True
    stock_strategy_min_trades_eval: int = Field(default=8, ge=1)
    # Revenge-trading guard: block new entries on the same symbol after N
    # consecutive losing exits within a session.
    stock_max_consecutive_losses_per_symbol: int = Field(default=2, ge=1)
    # Maximum bar age (seconds) tolerated by risk manager — blocks stale data.
    stock_max_bar_age_seconds: int = Field(default=120, ge=10)

    # --- Three-layer decision weights for stock trading ---
    stock_l1_weight: float = Field(default=0.50, ge=0.0, le=1.0)
    stock_l2_weight: float = Field(default=0.30, ge=0.0, le=1.0)
    stock_l3_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    # Final-score threshold below which trades are blocked. With dynamic weight
    # renormalization (inactive ML/LLM layers don't consume budget), a clean L1
    # signal must reach this confidence on its own; 0.50 keeps decent selectivity
    # while letting momentum/breakout setups through when ML/LLM are disabled.
    stock_min_final_score: float = Field(default=0.50, ge=0.0, le=1.0)

    # --- Local LLM (Jetson Orin Nano oriented) ---
    # Provider: "none", "llama_cpp", "ollama", "hosted_api" (compat shim)
    local_llm_provider: str = "none"
    # Used by llama_cpp provider — path to a quantized GGUF file.
    local_llm_model_path: str = "models/qwen2.5-3b-instruct-q4.gguf"
    # Used by ollama provider — model tag.
    local_llm_model_name: str = "qwen2.5:3b-instruct-q4_K_M"
    local_llm_endpoint: str = "http://127.0.0.1:11434"
    local_llm_context_size: int = Field(default=2048, ge=512)
    local_llm_threads: int = Field(default=4, ge=1)
    local_llm_gpu_layers: int = Field(default=20, ge=0)
    local_llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    local_llm_max_tokens: int = Field(default=512, ge=16)
    local_llm_timeout_seconds: float = Field(default=20.0, ge=0.05)
    local_llm_cache_ttl_seconds: int = Field(default=1800, ge=30)

    # --- Stock ML / regime ---
    stock_ml_model_path: str = "model_artifacts/stock_xgb_v1.pkl"
    stock_ml_min_samples: int = Field(default=200, ge=50)
    regime_lookback_bars: int = Field(default=60, ge=10)

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
        allowed = {"polymarket"}
        v = v.lower()
        # Backward-compat: Kalshi support was removed. Quietly migrate stale
        # deployment .env files (EXCHANGE=kalshi) to the only supported
        # prediction-market exchange instead of crashing startup — the field
        # is inactive in equities mode anyway.
        if v == "kalshi":
            import warnings

            warnings.warn(
                "EXCHANGE=kalshi is no longer supported; falling back to "
                "'polymarket'. Update your .env to remove this warning.",
                stacklevel=2,
            )
            return "polymarket"
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
        allowed = {"conservative", "moderate", "balanced", "aggressive"}
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
    def has_alpaca_credentials(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def approved_ticker_set(self) -> set[str]:
        """Set of upper-cased approved tickers from ``approved_stock_tickers``."""
        return {
            t.strip().upper()
            for t in self.approved_stock_tickers.split(",")
            if t.strip()
        }

    def is_ticker_approved(self, symbol: str) -> bool:
        approved = self.approved_ticker_set
        if not approved:
            return True
        return symbol.upper() in approved

    @property
    def has_credentials(self) -> bool:
        if self.asset_class == "equities":
            return self.has_alpaca_credentials
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
            raise RuntimeError(
                "Polymarket live trading requires PRIVATE_KEY, POLY_API_KEY, "
                "POLY_API_SECRET, and POLY_PASSPHRASE to be set in .env"
            )

    def require_credentials(self) -> None:
        """Backwards-compatible alias — delegates to full check."""
        self.require_live_trading()

    def __repr__(self) -> str:
        """Override repr to redact secrets — replaces secret values with ``***``."""
        _SECRETS = {
            "private_key", "poly_api_key", "poly_api_secret", "poly_passphrase",
            "llm_api_key",
            "newsapi_key", "alpaca_api_key", "alpaca_secret_key",
            "claude_api_key", "finnhub_api_key", "betstack_api_key",
        }
        safe_fields = {}
        for k, v in self.__dict__.items():
            if k.startswith("_"):
                continue
            if k in _SECRETS:
                if v:
                    safe_fields[k] = "***"
                continue
            safe_fields[k] = v
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
