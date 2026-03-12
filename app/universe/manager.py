"""
UniverseManager — top-level orchestrator for the universe selection pipeline.

Wires together: UniverseScanner → MarketFilter → OpportunityScorer → WatchlistManager

Runs on a schedule, producing an updated watchlist that the trading bot
consumes. Keeps universe selection cleanly separated from strategy
execution, risk management, and order placement.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.config.settings import Settings
from app.data.models import Market, OrderbookSnapshot
from app.exchanges.base import BaseMarketDataClient
from app.monitoring import get_logger
from app.universe.categories import CategoryConfig, CategoryPreferences
from app.universe.filters import FilterConfig, MarketFilter
from app.universe.scorer import OpportunityScorer, ScoredMarket, ScorerWeights
from app.universe.watchlist import WatchlistConfig, WatchlistManager

logger = get_logger(__name__)


class UniverseManager:
    """Orchestrates the full universe selection pipeline."""

    def __init__(self, settings: Settings, market_data_client: BaseMarketDataClient) -> None:
        self._settings = settings
        self._client = market_data_client

        from app.universe.scanner import UniverseScanner
        self._scanner = UniverseScanner(market_data_client)

        category_prefs = CategoryPreferences.from_settings(settings)
        self._categories = category_prefs

        self._filter = MarketFilter(FilterConfig(
            min_liquidity=settings.min_liquidity_threshold,
            max_spread=settings.max_spread_filter,
            min_volume=settings.min_volume_threshold,
            min_orderbook_depth=settings.min_orderbook_depth,
            min_time_to_resolution_hours=settings.min_time_to_resolution_hours,
            max_time_to_resolution_hours=settings.max_time_to_resolution_hours,
            allowed_categories=category_prefs.config.include_categories,
            excluded_categories=category_prefs.config.exclude_categories,
        ))

        cat_weights = category_prefs.config.category_weights or None
        self._scorer = OpportunityScorer(category_weights=cat_weights)

        self._watchlist = WatchlistManager(WatchlistConfig(
            max_tracked=settings.max_tracked_markets,
            max_subscribed=settings.max_subscribed_markets,
            max_trade_candidates=settings.max_trade_candidates,
            hysteresis_score=settings.watchlist_hysteresis_score,
            cooldown_seconds=settings.watchlist_cooldown_seconds,
        ))

        self._refresh_interval = settings.universe_refresh_seconds
        self._last_refresh: float = 0.0
        self._running = False
        self._stats: dict[str, Any] = {}

    @property
    def scanner(self) -> Any:
        return self._scanner

    @property
    def filter(self) -> MarketFilter:
        return self._filter

    @property
    def scorer(self) -> OpportunityScorer:
        return self._scorer

    @property
    def watchlist(self) -> WatchlistManager:
        return self._watchlist

    @property
    def categories(self) -> CategoryPreferences:
        return self._categories

    @property
    def active_markets(self) -> list[Market]:
        return self._watchlist.tracked_markets

    @property
    def subscribed_ids(self) -> list[str]:
        return self._watchlist.subscribed_ids

    @property
    def trade_candidate_ids(self) -> list[str]:
        return self._watchlist.trade_candidate_ids

    @property
    def stats(self) -> dict[str, Any]:
        return dict(self._stats)

    async def initial_selection(
        self,
        market_slugs: list[str] | None = None,
        books: dict[str, OrderbookSnapshot] | None = None,
    ) -> list[Market]:
        """Run the full pipeline once to select the initial watchlist."""
        active = await self._scanner.scan()

        if market_slugs:
            slug_set = set(market_slugs)
            active = [m for m in active if m.slug in slug_set]
            if not active:
                logger.warning("no_markets_match_slugs", slugs=market_slugs)
                return []
            scored = [ScoredMarket(market=m, score=1.0) for m in active]
            self._watchlist.update(scored)
            self._log_stats(len(self._scanner.known_markets), active, [], scored)
            return self._watchlist.tracked_markets

        return await self._evaluate_and_update(active, books)

    async def refresh(
        self,
        books: dict[str, OrderbookSnapshot] | None = None,
    ) -> dict[str, list[str]]:
        """Re-scan the universe and update the watchlist."""
        active = await self._scanner.scan()
        await self._evaluate_and_update(active, books)
        self._last_refresh = time.time()
        return self._watchlist.get_watchlist_summary()

    async def _evaluate_and_update(
        self,
        active_markets: list[Market],
        books: dict[str, OrderbookSnapshot] | None = None,
    ) -> list[Market]:
        """Filter, score, and update the watchlist."""
        books = books or {}
        eligible: list[Market] = []
        filtered_out: list[tuple[str, str]] = []

        for market in active_markets:
            if not self._categories.is_allowed(market):
                filtered_out.append((market.market_id, "category_preference"))
                continue

            book = books.get(market.market_id)
            metadata = self._extract_metadata(market)
            result = self._filter.apply_all(market, book, metadata)

            if not result.passed:
                filtered_out.append((market.market_id, result.reason))
                continue

            eligible.append(market)

        for mid in self._scanner.resolved:
            self._watchlist.force_remove(mid)

        metadata_map: dict[str, dict[str, Any]] = {}
        for m in eligible:
            metadata_map[m.market_id] = self._extract_metadata(m)

        scored = self._scorer.score_batch(eligible, books, metadata_map)

        changes = self._watchlist.update(scored)

        self._log_stats(len(self._scanner.known_markets), eligible, filtered_out, scored)

        return self._watchlist.tracked_markets

    def should_refresh(self) -> bool:
        if self._last_refresh == 0.0:
            return True
        return (time.time() - self._last_refresh) >= self._refresh_interval

    async def run_refresh_loop(self) -> None:
        """Background loop that periodically refreshes the universe."""
        self._running = True
        while self._running:
            await asyncio.sleep(self._refresh_interval)
            if not self._running:
                break
            try:
                await self.refresh()
            except Exception as exc:
                logger.error("universe_refresh_failed", error=str(exc))

    def stop(self) -> None:
        self._running = False

    def _extract_metadata(self, market: Market) -> dict[str, Any]:
        """Extract scoring metadata from a market's exchange_data."""
        ed = market.exchange_data or {}
        return {
            "volume": ed.get("volume", 0),
            "volume_24h": ed.get("volume_24h", 0),
            "open_interest": ed.get("open_interest", 0),
            "liquidity": ed.get("liquidity", 0),
            "spread": ed.get("spread"),
            "trade_count": ed.get("trade_count", 0),
            "momentum": ed.get("momentum", 0),
            "volatility": ed.get("volatility", 0),
            "category": ed.get("category", ""),
        }

    def _log_stats(
        self,
        total_known: int,
        eligible: list[Market],
        filtered_out: list[tuple[str, str]],
        scored: list[ScoredMarket],
    ) -> None:
        filter_reasons: dict[str, int] = {}
        for _, reason in filtered_out:
            key = reason.split(":")[0] if ":" in reason else reason
            filter_reasons[key] = filter_reasons.get(key, 0) + 1

        top_scores = [(sm.market_id, round(sm.score, 4)) for sm in scored[:10]]
        category_dist = self._categories.get_category_distribution(self._watchlist.tracked_markets)

        self._stats = {
            "total_discovered": total_known,
            "total_eligible": len(eligible),
            "total_filtered_out": len(filtered_out),
            "filter_reasons": filter_reasons,
            "total_scored": len(scored),
            "top_scores": top_scores,
            "watchlist_size": len(self._watchlist.tracked_ids),
            "category_distribution": category_dist,
        }

        logger.info(
            "universe_selection_complete",
            total_discovered=total_known,
            eligible=len(eligible),
            filtered_out=len(filtered_out),
            watchlist_size=len(self._watchlist.tracked_ids),
            filter_reasons=filter_reasons,
            top_scores=top_scores[:5],
            category_distribution=category_dist,
        )
