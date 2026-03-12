"""
CategoryPreferences — manages category-level preferences and constraints.

Supports:
  - Auto mode (all categories eligible)
  - Include/exclude lists
  - Per-category weights for scoring
  - Per-category maximum tracked markets
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import Settings
from app.data.models import Market
from app.monitoring import get_logger

logger = get_logger(__name__)


@dataclass
class CategoryConfig:
    mode: str = "auto"
    include_categories: set[str] = field(default_factory=set)
    exclude_categories: set[str] = field(default_factory=set)
    category_weights: dict[str, float] = field(default_factory=dict)
    max_per_category: dict[str, int] = field(default_factory=dict)
    default_max_per_category: int = 0


class CategoryPreferences:
    """Manages category filtering and weighting."""

    def __init__(self, config: CategoryConfig) -> None:
        self._config = config

    @property
    def config(self) -> CategoryConfig:
        return self._config

    @classmethod
    def from_settings(cls, settings: Settings) -> CategoryPreferences:
        include = set()
        if settings.include_categories:
            include = {c.strip().lower() for c in settings.include_categories.split(",") if c.strip()}

        exclude = set()
        if settings.exclude_categories:
            exclude = {c.strip().lower() for c in settings.exclude_categories.split(",") if c.strip()}

        weights: dict[str, float] = {}
        if settings.category_weights_json:
            try:
                raw = json.loads(settings.category_weights_json)
                weights = {k.lower(): float(v) for k, v in raw.items()}
            except (json.JSONDecodeError, ValueError, AttributeError) as exc:
                logger.warning("category_weights_parse_error", error=str(exc))

        return cls(CategoryConfig(
            mode=settings.universe_mode,
            include_categories=include,
            exclude_categories=exclude,
            category_weights=weights,
        ))

    def is_allowed(self, market: Market) -> bool:
        if self._config.mode == "auto" and not self._config.include_categories and not self._config.exclude_categories:
            return True

        cat = _get_category(market)

        if self._config.exclude_categories and cat in self._config.exclude_categories:
            return False

        if self._config.include_categories:
            if not cat:
                return len(self._config.include_categories) == 0
            return cat in self._config.include_categories

        return True

    def get_weight(self, market: Market) -> float:
        cat = _get_category(market)
        if not cat or not self._config.category_weights:
            return 1.0
        return self._config.category_weights.get(cat, 1.0)

    def get_max_for_category(self, category: str) -> int:
        cat = category.lower()
        if cat in self._config.max_per_category:
            return self._config.max_per_category[cat]
        return self._config.default_max_per_category

    def get_category_distribution(self, markets: list[Market]) -> dict[str, int]:
        dist: dict[str, int] = {}
        for m in markets:
            cat = _get_category(m) or "uncategorized"
            dist[cat] = dist.get(cat, 0) + 1
        return dist


def _get_category(market: Market) -> str:
    cat = getattr(market, "category", "")
    if cat:
        return cat.lower()
    exchange_data = getattr(market, "exchange_data", {}) or {}
    return (exchange_data.get("category", "") or "").lower()
