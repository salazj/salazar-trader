"""
WatchlistManager — manages the active market watchlist with hysteresis.

Responsibilities:
  - Select top N markets by score
  - Support configurable limits (tracked, subscribed, trade candidates)
  - Rotate markets in and out over time
  - Prevent thrashing with hysteresis and cooldown rules
  - Log why markets are added or removed
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.data.models import Market
from app.monitoring import get_logger
from app.universe.scorer import ScoredMarket

logger = get_logger(__name__)


@dataclass
class WatchlistChange:
    timestamp: float
    market_id: str
    action: str  # "added" or "removed"
    reason: str
    score: float = 0.0


@dataclass
class WatchlistConfig:
    max_tracked: int = 50
    max_subscribed: int = 20
    max_trade_candidates: int = 10
    hysteresis_score: float = 0.05
    cooldown_seconds: float = 600.0


class WatchlistManager:
    """Dynamic watchlist with hysteresis-based rotation."""

    def __init__(self, config: WatchlistConfig) -> None:
        self._config = config
        self._tracked: dict[str, ScoredMarket] = {}
        self._subscribed: set[str] = set()
        self._trade_candidates: set[str] = set()
        self._cooldowns: dict[str, float] = {}
        self._change_log: list[WatchlistChange] = []
        self._scores_history: dict[str, list[float]] = {}

    @property
    def config(self) -> WatchlistConfig:
        return self._config

    @property
    def tracked_ids(self) -> list[str]:
        return list(self._tracked.keys())

    @property
    def tracked_markets(self) -> list[Market]:
        return [sm.market for sm in self._tracked.values()]

    @property
    def subscribed_ids(self) -> list[str]:
        return list(self._subscribed)

    @property
    def trade_candidate_ids(self) -> list[str]:
        return list(self._trade_candidates)

    @property
    def change_log(self) -> list[WatchlistChange]:
        return list(self._change_log)

    def get_score(self, market_id: str) -> float | None:
        sm = self._tracked.get(market_id)
        return sm.score if sm else None

    def update(self, scored_markets: list[ScoredMarket]) -> dict[str, list[str]]:
        """
        Update the watchlist based on new scored markets.
        Returns dict with 'added', 'removed', and 'retained' lists.
        """
        now = time.time()
        score_map = {sm.market_id: sm for sm in scored_markets}
        added: list[str] = []
        removed: list[str] = []
        retained: list[str] = []

        for sm in scored_markets:
            mid = sm.market_id
            hist = self._scores_history.get(mid, [])
            hist.append(sm.score)
            if len(hist) > 20:
                hist = hist[-20:]
            self._scores_history[mid] = hist

        sorted_markets = sorted(scored_markets, key=lambda s: s.score, reverse=True)

        new_tracked: dict[str, ScoredMarket] = {}

        for sm in sorted_markets[:self._config.max_tracked]:
            mid = sm.market_id
            if mid in self._tracked:
                new_tracked[mid] = sm
                retained.append(mid)
            elif self._is_eligible_for_addition(mid, sm.score, now):
                new_tracked[mid] = sm
                added.append(mid)
                self._log_change(now, mid, "added", f"score={sm.score:.4f}", sm.score)
            elif len(new_tracked) < self._config.max_tracked:
                new_tracked[mid] = sm
                added.append(mid)
                self._log_change(now, mid, "added", f"score={sm.score:.4f} (no cooldown block)", sm.score)

        for mid in self._tracked:
            if mid not in new_tracked:
                if mid in score_map:
                    current_score = score_map[mid].score
                else:
                    current_score = 0.0

                min_threshold = self._get_min_threshold_for_removal(sorted_markets, new_tracked)
                if current_score + self._config.hysteresis_score >= min_threshold:
                    if len(new_tracked) < self._config.max_tracked:
                        new_tracked[mid] = self._tracked[mid]
                        retained.append(mid)
                        continue

                removed.append(mid)
                self._cooldowns[mid] = now
                self._log_change(now, mid, "removed", f"score={current_score:.4f} below threshold", current_score)

        self._tracked = new_tracked

        self._subscribed = set(list(new_tracked.keys())[:self._config.max_subscribed])
        self._trade_candidates = set(list(new_tracked.keys())[:self._config.max_trade_candidates])

        logger.info(
            "watchlist_updated",
            tracked=len(self._tracked),
            subscribed=len(self._subscribed),
            trade_candidates=len(self._trade_candidates),
            added=len(added),
            removed=len(removed),
            retained=len(retained),
        )

        return {"added": added, "removed": removed, "retained": retained}

    def force_add(self, market_id: str, scored: ScoredMarket) -> None:
        """Force-add a market bypassing score/cooldown checks."""
        self._tracked[market_id] = scored
        self._log_change(time.time(), market_id, "added", "force_added", scored.score)

    def force_remove(self, market_id: str) -> None:
        """Force-remove a market."""
        if market_id in self._tracked:
            self._tracked.pop(market_id)
            self._subscribed.discard(market_id)
            self._trade_candidates.discard(market_id)
            self._log_change(time.time(), market_id, "removed", "force_removed", 0.0)

    def is_tracked(self, market_id: str) -> bool:
        return market_id in self._tracked

    def is_subscribed(self, market_id: str) -> bool:
        return market_id in self._subscribed

    def get_watchlist_summary(self) -> dict[str, Any]:
        """Observability snapshot."""
        return {
            "tracked_count": len(self._tracked),
            "subscribed_count": len(self._subscribed),
            "trade_candidate_count": len(self._trade_candidates),
            "tracked_ids": self.tracked_ids,
            "scores": {mid: sm.score for mid, sm in self._tracked.items()},
            "recent_changes": [
                {"market_id": c.market_id, "action": c.action, "reason": c.reason, "score": c.score}
                for c in self._change_log[-20:]
            ],
        }

    def _is_eligible_for_addition(self, market_id: str, score: float, now: float) -> bool:
        cooldown_until = self._cooldowns.get(market_id, 0.0)
        if now < cooldown_until + self._config.cooldown_seconds:
            return False
        return True

    def _get_min_threshold_for_removal(
        self, sorted_markets: list[ScoredMarket], current_tracked: dict[str, ScoredMarket]
    ) -> float:
        if len(current_tracked) < self._config.max_tracked:
            return 0.0
        scores = [sm.score for sm in current_tracked.values()]
        if not scores:
            return 0.0
        return min(scores)

    def _log_change(self, ts: float, market_id: str, action: str, reason: str, score: float) -> None:
        change = WatchlistChange(
            timestamp=ts,
            market_id=market_id,
            action=action,
            reason=reason,
            score=score,
        )
        self._change_log.append(change)
        if len(self._change_log) > 500:
            self._change_log = self._change_log[-200:]
        logger.info(
            "watchlist_change",
            market_id=market_id,
            action=action,
            reason=reason,
            score=round(score, 4),
        )
