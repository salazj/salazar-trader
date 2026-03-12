"""
Base strategy interface and registry.

Every strategy must implement `generate_signal()`. Strategies never place
orders directly — they only produce Signal objects for the execution engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.data.models import MarketFeatures, PortfolioSnapshot, Signal
from app.config.settings import Settings
from app.monitoring import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class BaseStrategy(ABC):
    """Abstract base for all trading strategies."""

    name: str = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abstractmethod
    def generate_signal(
        self,
        features: MarketFeatures,
        portfolio: PortfolioSnapshot,
    ) -> Signal | None:
        """
        Evaluate market state and portfolio, return a Signal or None.

        Must NOT perform any side effects (no order placement, no state mutation
        outside the strategy's own internal state).
        """
        ...


class StrategyRegistry:
    """Registry mapping strategy names to classes. Supports lazy loading."""

    _registry: dict[str, type[BaseStrategy]] = {}

    @classmethod
    def register(cls, strategy_class: type[BaseStrategy]) -> type[BaseStrategy]:
        cls._registry[strategy_class.name] = strategy_class
        return strategy_class

    @classmethod
    def get(cls, name: str) -> type[BaseStrategy]:
        if name not in cls._registry:
            # Trigger imports to populate registry
            _import_all_strategies()
            if name not in cls._registry:
                raise ValueError(
                    f"Unknown strategy '{name}'. Available: {list(cls._registry.keys())}"
                )
        return cls._registry[name]

    @classmethod
    def available(cls) -> list[str]:
        _import_all_strategies()
        return list(cls._registry.keys())


def _import_all_strategies() -> None:
    """Force-import all strategy modules to trigger registration."""
    import app.strategies.passive_market_maker  # noqa: F401
    import app.strategies.momentum_scalper  # noqa: F401
    import app.strategies.event_probability_model  # noqa: F401
    import app.strategies.sentiment_adapter  # noqa: F401
    import app.strategies.prediction_value  # noqa: F401
