"""
MarketFilter — hard filters that reject markets from consideration.

Each filter returns a (passed: bool, reason: str) tuple.
A market must pass ALL filters to remain eligible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.data.models import Market, OrderbookSnapshot
from app.monitoring import get_logger

logger = get_logger(__name__)


@dataclass
class FilterResult:
    passed: bool
    reason: str = ""
    market_id: str = ""


@dataclass
class FilterConfig:
    min_liquidity: float = 10.0
    max_spread: float = 0.20
    min_volume: float = 0.0
    min_orderbook_depth: float = 5.0
    min_time_to_resolution_hours: float = 1.0
    max_time_to_resolution_hours: float = 8760.0
    allowed_categories: set[str] = field(default_factory=set)
    excluded_categories: set[str] = field(default_factory=set)
    allowed_exchanges: set[str] = field(default_factory=set)
    excluded_exchanges: set[str] = field(default_factory=set)
    max_stale_seconds: float = 3600.0


class MarketFilter:
    """Applies hard filters to markets. Returns pass/fail with reasons."""

    def __init__(self, config: FilterConfig) -> None:
        self._config = config

    @property
    def config(self) -> FilterConfig:
        return self._config

    def apply_all(
        self,
        market: Market,
        book: OrderbookSnapshot | None = None,
        market_metadata: dict[str, Any] | None = None,
    ) -> FilterResult:
        """Run all filters. Returns the first failure, or passed=True."""
        metadata = market_metadata or {}

        checks = [
            self._check_active(market),
            self._check_exchange(market),
            self._check_categories(market),
            self._check_time_to_resolution(market),
            self._check_liquidity(book, metadata),
            self._check_spread(book, metadata),
            self._check_volume(metadata),
            self._check_orderbook_depth(book, metadata),
            self._check_stale(metadata),
        ]

        for result in checks:
            if not result.passed:
                result.market_id = market.market_id
                return result

        return FilterResult(passed=True, market_id=market.market_id)

    def _check_active(self, market: Market) -> FilterResult:
        if not market.active:
            return FilterResult(False, "market_inactive")
        return FilterResult(True)

    def _check_exchange(self, market: Market) -> FilterResult:
        if self._config.allowed_exchanges and market.exchange not in self._config.allowed_exchanges:
            return FilterResult(False, f"exchange_not_allowed:{market.exchange}")
        if market.exchange in self._config.excluded_exchanges:
            return FilterResult(False, f"exchange_excluded:{market.exchange}")
        return FilterResult(True)

    def _check_categories(self, market: Market) -> FilterResult:
        cat = _get_category(market)
        if self._config.allowed_categories and cat and cat not in self._config.allowed_categories:
            return FilterResult(False, f"category_not_allowed:{cat}")
        if cat and cat in self._config.excluded_categories:
            return FilterResult(False, f"category_excluded:{cat}")
        return FilterResult(True)

    def _check_time_to_resolution(self, market: Market) -> FilterResult:
        if not market.end_date:
            return FilterResult(True)
        try:
            end_str = market.end_date
            if isinstance(end_str, str):
                end_str = end_str.replace("Z", "+00:00")
                end_dt = datetime.fromisoformat(end_str)
            else:
                end_dt = end_str
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            hours_remaining = (end_dt - now).total_seconds() / 3600.0
            if hours_remaining < self._config.min_time_to_resolution_hours:
                return FilterResult(False, f"too_close_to_resolution:{hours_remaining:.1f}h")
            if hours_remaining > self._config.max_time_to_resolution_hours:
                return FilterResult(False, f"too_far_from_resolution:{hours_remaining:.1f}h")
        except (ValueError, TypeError):
            pass
        return FilterResult(True)

    def _check_liquidity(
        self, book: OrderbookSnapshot | None, metadata: dict[str, Any]
    ) -> FilterResult:
        liquidity = metadata.get("liquidity")
        if book is not None:
            bid_liq = sum(l.size for l in book.bids)
            ask_liq = sum(l.size for l in book.asks)
            book_liq = bid_liq + ask_liq
            liquidity = max(liquidity or 0.0, book_liq)
        if liquidity is None or liquidity == 0:
            return FilterResult(True)
        if self._config.min_liquidity > 0 and liquidity < self._config.min_liquidity:
            return FilterResult(False, f"insufficient_liquidity:{liquidity:.1f}")
        return FilterResult(True)

    def _check_spread(
        self, book: OrderbookSnapshot | None, metadata: dict[str, Any]
    ) -> FilterResult:
        spread = metadata.get("spread")
        if book is not None and book.bids and book.asks:
            spread = book.asks[0].price - book.bids[0].price
        if spread is not None and spread > self._config.max_spread:
            return FilterResult(False, f"spread_too_wide:{spread:.4f}")
        return FilterResult(True)

    def _check_volume(self, metadata: dict[str, Any]) -> FilterResult:
        volume = metadata.get("volume", metadata.get("volume_24h", 0.0))
        if volume is None:
            volume = 0.0
        if self._config.min_volume > 0 and float(volume) < self._config.min_volume:
            return FilterResult(False, f"insufficient_volume:{volume}")
        return FilterResult(True)

    def _check_orderbook_depth(
        self, book: OrderbookSnapshot | None, metadata: dict[str, Any]
    ) -> FilterResult:
        depth = metadata.get("depth", 0.0)
        if book is not None:
            depth = max(depth, len(book.bids) + len(book.asks))
        elif depth == 0.0:
            return FilterResult(True)
        if self._config.min_orderbook_depth > 0 and depth < self._config.min_orderbook_depth:
            return FilterResult(False, f"insufficient_depth:{depth}")
        return FilterResult(True)

    def _check_stale(self, metadata: dict[str, Any]) -> FilterResult:
        last_trade_seconds = metadata.get("seconds_since_last_trade")
        if last_trade_seconds is not None and last_trade_seconds > self._config.max_stale_seconds:
            return FilterResult(False, f"stale_market:{last_trade_seconds:.0f}s")
        return FilterResult(True)


def _get_category(market: Market) -> str:
    cat = getattr(market, "category", "")
    if cat:
        return cat.lower()
    exchange_data = getattr(market, "exchange_data", {}) or {}
    return (exchange_data.get("category", "") or "").lower()
