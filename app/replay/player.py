"""
Replay Player

Replays captured WebSocket/market snapshots offline, feeding them through
the same data pipeline and strategy logic that runs in production.

This allows testing strategy behavior without risking money and without
needing a live connection.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.data.features import FeatureEngine
from app.data.models import MarketFeatures, OrderbookSnapshot, PriceLevel, Trade, Side
from app.data.orderbook import OrderbookManager
from app.monitoring import get_logger
from app.portfolio.tracker import PortfolioTracker
from app.risk.manager import RiskManager
from app.strategies.base import BaseStrategy

logger = get_logger(__name__)


class ReplayEvent:
    """A single timestamped event from a recorded session."""

    def __init__(self, timestamp: datetime, event_type: str, data: dict[str, Any]) -> None:
        self.timestamp = timestamp
        self.event_type = event_type
        self.data = data


class ReplayPlayer:
    """
    Drives a strategy through recorded events as if they were live.

    Usage:
        player = ReplayPlayer(strategy, settings)
        events = player.load_events("path/to/events.jsonl")
        results = player.play(events)
    """

    def __init__(self, strategy: BaseStrategy, settings: Settings) -> None:
        self._strategy = strategy
        self._settings = settings
        self._orderbook = OrderbookManager()
        self._feature_engines: dict[str, FeatureEngine] = {}
        self._portfolio = PortfolioTracker(settings)
        self._risk = RiskManager(settings)
        self._signals: list[dict[str, Any]] = []

    @staticmethod
    def load_events(path: Path) -> list[ReplayEvent]:
        """Load events from a JSONL file (one JSON object per line)."""
        events: list[ReplayEvent] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                ts = datetime.fromisoformat(obj["timestamp"])
                events.append(ReplayEvent(
                    timestamp=ts,
                    event_type=obj["event_type"],
                    data=obj.get("data", {}),
                ))
        events.sort(key=lambda e: e.timestamp)
        logger.info("replay_events_loaded", count=len(events), path=str(path))
        return events

    def play(self, events: list[ReplayEvent], speed: float = 0.0) -> dict[str, Any]:
        """
        Replay events through the strategy.

        Args:
            events: Time-sorted list of events.
            speed: Playback speed multiplier. 0 = instant (no delay).

        Returns:
            Summary dict with signals generated and portfolio state.
        """
        logger.info("replay_started", events=len(events), strategy=self._strategy.name)

        for event in events:
            self._process_event(event)

        summary = {
            "strategy": self._strategy.name,
            "events_processed": len(events),
            "signals_generated": len(self._signals),
            "portfolio": self._portfolio.export_summary(),
            "signals": self._signals,
        }

        logger.info(
            "replay_completed",
            events=len(events),
            signals=len(self._signals),
        )
        return summary

    def _process_event(self, event: ReplayEvent) -> None:
        """Route event to appropriate handler."""
        if event.event_type == "book_snapshot":
            self._handle_book_snapshot(event)
        elif event.event_type == "book_delta":
            self._handle_book_delta(event)
        elif event.event_type == "trade":
            self._handle_trade(event)

    def _handle_book_snapshot(self, event: ReplayEvent) -> None:
        d = event.data
        iid = d.get("instrument_id", d.get("token_id", ""))
        market_id = d.get("market_id", "")
        self._orderbook.apply_snapshot(
            market_id=market_id,
            instrument_id=iid,
            bids=d.get("bids", []),
            asks=d.get("asks", []),
        )
        self._maybe_generate_signal(iid, market_id)

    def _handle_book_delta(self, event: ReplayEvent) -> None:
        d = event.data
        iid = d.get("instrument_id", d.get("token_id", ""))
        self._orderbook.apply_delta(
            instrument_id=iid,
            bid_updates=d.get("bids"),
            ask_updates=d.get("asks"),
        )

    def _handle_trade(self, event: ReplayEvent) -> None:
        d = event.data
        iid = d.get("instrument_id", d.get("token_id", ""))
        market_id = d.get("market_id", "")

        trade = Trade(
            market_id=market_id,
            token_id=iid,
            instrument_id=iid,
            price=float(d["price"]),
            size=float(d["size"]),
            side=Side(d.get("side", "BUY")),
        )

        engine = self._get_feature_engine(market_id, iid)
        engine.add_trade(trade)

    def _maybe_generate_signal(self, instrument_id: str, market_id: str) -> None:
        book = self._orderbook.get_snapshot(instrument_id)
        if book is None:
            return

        engine = self._get_feature_engine(market_id, instrument_id)
        features = engine.compute(book)
        portfolio_snap = self._portfolio.get_snapshot()

        signal = self._strategy.generate_signal(features, portfolio_snap)
        if signal is not None:
            self._signals.append({
                "timestamp": signal.timestamp.isoformat() if hasattr(signal.timestamp, "isoformat") else str(signal.timestamp),
                "action": signal.action.value,
                "confidence": signal.confidence,
                "price": signal.suggested_price,
                "size": signal.suggested_size,
                "rationale": signal.rationale,
            })

    def _get_feature_engine(self, market_id: str, instrument_id: str) -> FeatureEngine:
        if instrument_id not in self._feature_engines:
            self._feature_engines[instrument_id] = FeatureEngine(market_id, instrument_id=instrument_id)
        return self._feature_engines[instrument_id]

    def save_results(self, results: dict[str, Any], output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info("replay_results_saved", path=str(output_path))
