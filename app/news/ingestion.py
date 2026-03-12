"""
News ingestion service: polls registered NLP providers, deduplicates,
caches recent items, and feeds them into the NLP pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from datetime import timedelta
from typing import Callable

from app.utils.helpers import utc_now

from app.data.models import Market
from app.monitoring import get_logger
from app.monitoring.logger import metrics
from app.news.models import NewsItem
from app.nlp.pipeline import NlpPipeline
from app.nlp.providers.base import BaseNlpProvider
from app.nlp.signals import NlpSignal

logger = get_logger(__name__)


class NewsIngestionService:
    """Polls providers, deduplicates, and generates NLP signals."""

    def __init__(
        self,
        providers: list[BaseNlpProvider] | None = None,
        pipeline: NlpPipeline | None = None,
        poll_interval: float = 300.0,
        cache_max_size: int = 1000,
        cache_ttl: float = 3600.0,
    ) -> None:
        self._providers = providers or []
        self._pipeline = pipeline or NlpPipeline()
        self._poll_interval = poll_interval
        self._cache_max_size = cache_max_size
        self._cache_ttl = cache_ttl

        self._seen_hashes: OrderedDict[str, float] = OrderedDict()
        self._latest_signals: list[NlpSignal] = []
        self._max_signal_age = poll_interval * 3
        self._running = False

        self._get_markets: Callable[[], list[Market]] | None = None

    def set_market_provider(self, fn: Callable[[], list[Market]]) -> None:
        self._get_markets = fn

    def register_provider(self, provider: BaseNlpProvider) -> None:
        self._providers.append(provider)

    async def start(self) -> None:
        self._running = True
        logger.info(
            "news_ingestion_started",
            providers=[p.name for p in self._providers],
            poll_interval=self._poll_interval,
        )
        while self._running:
            try:
                await self._poll_cycle()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("news_ingestion_poll_error")
            await asyncio.sleep(self._poll_interval)

    async def stop(self) -> None:
        self._running = False
        logger.info("news_ingestion_stopped")

    async def poll_once(self) -> list[NlpSignal]:
        """Single poll cycle — useful for testing and manual invocation."""
        return await self._poll_cycle()

    def get_latest_signals(self) -> list[NlpSignal]:
        """Return all buffered signals without clearing.

        Signals are kept for multiple intelligence loop iterations so every
        market gets a chance to match.  Old signals are pruned in
        ``_poll_cycle`` when fresh ones arrive.
        """
        return list(self._latest_signals)

    async def _poll_cycle(self) -> list[NlpSignal]:
        all_items: list[NewsItem] = []
        for provider in self._providers:
            if not provider.is_available():
                continue
            try:
                items = await provider.fetch_items()
                metrics.increment("news_items_fetched", len(items))
                deduped = self._deduplicate(items)
                all_items.extend(deduped)
            except Exception:
                logger.exception("news_provider_error", provider=provider.name)
                metrics.increment("news_provider_errors")

        if not all_items:
            return []

        markets = self._get_markets() if self._get_markets else []
        signals = self._pipeline.process_batch(all_items, markets)

        cutoff = utc_now() - timedelta(seconds=self._max_signal_age)
        self._latest_signals = [
            s for s in self._latest_signals
            if s.source_timestamp > cutoff
        ]
        self._latest_signals.extend(signals)
        metrics.increment("nlp_signals_generated", len(signals))

        logger.info(
            "news_poll_complete",
            items_fetched=len(all_items),
            signals_generated=len(signals),
        )
        return signals

    def _deduplicate(self, items: list[NewsItem]) -> list[NewsItem]:
        now = time.monotonic()
        self._evict_stale(now)

        unique: list[NewsItem] = []
        for item in items:
            h = self._content_hash(item.text)
            if h in self._seen_hashes:
                metrics.increment("news_duplicates_skipped")
                continue
            self._seen_hashes[h] = now
            if len(self._seen_hashes) > self._cache_max_size:
                self._seen_hashes.popitem(last=False)
            unique.append(item)
        return unique

    def _evict_stale(self, now: float) -> None:
        stale = [k for k, t in self._seen_hashes.items() if now - t > self._cache_ttl]
        for k in stale:
            del self._seen_hashes[k]

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]
