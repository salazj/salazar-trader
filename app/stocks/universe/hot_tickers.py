"""Hot-ticker tracker: maintain a TTL'd set of tickers with recent newsflow.

Used by the news-driven universe selector. Every time the NLP pipeline
classifies a news item, the result is forwarded here. We resolve the
classifier's ``entities`` (and the headline body) to tickers, score
their freshness, and expose the top-N as candidates to trade.

Each ticker accumulates a simple weighted score::

    score = sum_over_recent_news(
        relevance * confidence * direction_weight
    )

where ``direction_weight`` boosts bullish/bearish over neutral. Stale
news ages out of the window via ``ttl_minutes``.

The tracker is **pure**: no network, no disk, no global state. The
trading loop owns the instance.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.nlp.signals import ClassificationResult, SentimentDirection
from app.stocks.universe.name_resolver import NameResolver


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HotTickerEntry:
    """One news-driven observation for a ticker."""

    ticker: str
    headline: str
    sentiment: SentimentDirection
    sentiment_score: float
    urgency: float
    relevance: float
    confidence: float
    timestamp: datetime


@dataclass
class HotTicker:
    """Aggregated state for one hot ticker."""

    ticker: str
    score: float = 0.0
    last_seen: datetime = field(default_factory=_utc_now)
    latest_headline: str = ""
    sentiment_score: float = 0.0
    entry_count: int = 0
    bullish_count: int = 0
    bearish_count: int = 0

    @property
    def net_sentiment(self) -> SentimentDirection:
        if self.bullish_count > self.bearish_count:
            return SentimentDirection.BULLISH
        if self.bearish_count > self.bullish_count:
            return SentimentDirection.BEARISH
        return SentimentDirection.NEUTRAL


class HotTickerTracker:
    """Maintains a TTL'd, score-ranked set of news-hot tickers."""

    def __init__(
        self,
        resolver: NameResolver,
        ttl_minutes: int = 60,
        max_tickers: int = 20,
        min_confidence: float = 0.3,
        min_relevance: float = 0.2,
    ) -> None:
        self._resolver = resolver
        self._ttl = timedelta(minutes=ttl_minutes)
        self._max_tickers = max_tickers
        self._min_confidence = min_confidence
        self._min_relevance = min_relevance
        self._entries: deque[HotTickerEntry] = deque(maxlen=2000)
        self._state: dict[str, HotTicker] = {}
        # ingest() runs on the NLP worker thread (asyncio.to_thread) while the
        # trading / universe-refresh loops read on the event loop. Guard all
        # access to _entries/_state. Reentrant so internal _prune calls are ok.
        self._lock = threading.RLock()

    def ingest(
        self,
        classification: ClassificationResult,
        headline: str,
        *,
        now: datetime | None = None,
    ) -> set[str]:
        """Process one classified news item; returns the tickers touched."""
        if classification.confidence < self._min_confidence:
            return set()
        if classification.relevance < self._min_relevance:
            return set()

        now = now or _utc_now()
        candidates = self._resolver.resolve(classification.entities)
        if not candidates:
            candidates = self._resolver.resolve_text(headline)
        if not candidates:
            return set()

        touched: set[str] = set()
        with self._lock:
            for ticker in candidates:
                if ticker not in self._resolver.allowed_tickers:
                    continue
                entry = HotTickerEntry(
                    ticker=ticker,
                    headline=headline[:200],
                    sentiment=classification.sentiment,
                    sentiment_score=classification.sentiment_score,
                    urgency=classification.urgency,
                    relevance=classification.relevance,
                    confidence=classification.confidence,
                    timestamp=now,
                )
                self._entries.append(entry)
                self._update_state(entry, now)
                touched.add(ticker)
            self._prune(now)
        return touched

    def _update_state(self, entry: HotTickerEntry, now: datetime) -> None:
        state = self._state.get(entry.ticker)
        if state is None:
            state = HotTicker(ticker=entry.ticker)
            self._state[entry.ticker] = state

        direction_weight = 1.0
        if entry.sentiment == SentimentDirection.BULLISH:
            direction_weight = 1.0 + 0.5 * entry.urgency
            state.bullish_count += 1
        elif entry.sentiment == SentimentDirection.BEARISH:
            direction_weight = 1.0 + 0.5 * entry.urgency
            state.bearish_count += 1
        else:
            direction_weight = 0.5

        score_delta = (
            entry.relevance * entry.confidence * direction_weight
        )

        state.score += score_delta
        state.last_seen = now
        state.latest_headline = entry.headline
        state.sentiment_score = (
            0.7 * state.sentiment_score + 0.3 * entry.sentiment_score
        )
        state.entry_count += 1

    def _prune(self, now: datetime | None = None) -> None:
        now = now or _utc_now()
        cutoff = now - self._ttl
        kept_entries: deque[HotTickerEntry] = deque(
            (e for e in self._entries if e.timestamp >= cutoff),
            maxlen=self._entries.maxlen,
        )
        self._entries = kept_entries

        for ticker, state in list(self._state.items()):
            if state.last_seen < cutoff:
                del self._state[ticker]
                continue
            decay = max(
                0.0,
                1.0 - (now - state.last_seen).total_seconds()
                / self._ttl.total_seconds(),
            )
            state.score *= decay

    def get_hot_tickers(
        self, limit: int | None = None, *, now: datetime | None = None
    ) -> list[str]:
        """Return ticker symbols ordered by current score (high → low)."""
        with self._lock:
            self._prune(now)
            ranked = sorted(
                self._state.values(),
                key=lambda s: s.score,
                reverse=True,
            )
            cap = limit if limit is not None else self._max_tickers
            return [s.ticker for s in ranked[:cap]]

    def get_state(self, ticker: str) -> HotTicker | None:
        with self._lock:
            return self._state.get(ticker.upper())

    def latest_headline(self, ticker: str) -> str:
        """Return the most recent headline that touched this ticker."""
        with self._lock:
            state = self._state.get(ticker.upper())
            return state.latest_headline if state else ""

    def snapshot(self) -> list[HotTicker]:
        """Return all currently-tracked tickers (unsorted, post-prune)."""
        with self._lock:
            self._prune()
            return list(self._state.values())

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._state.clear()

    def ingest_many(
        self, items: Iterable[tuple[ClassificationResult, str]]
    ) -> set[str]:
        touched: set[str] = set()
        for classification, headline in items:
            touched |= self.ingest(classification, headline)
        return touched
