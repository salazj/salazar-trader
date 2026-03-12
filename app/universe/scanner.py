"""
UniverseScanner — fetches and normalizes the full active market universe.

Responsibilities:
  - Fetch all active markets from the exchange adapter
  - Normalize metadata into a consistent format
  - Detect newly listed markets
  - Detect resolved/inactive markets
  - Track the full known universe over time
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.data.models import Market
from app.exchanges.base import BaseMarketDataClient
from app.monitoring import get_logger
from app.utils.helpers import utc_now

logger = get_logger(__name__)


class UniverseScanner:
    """Discovers and tracks the full market universe from the exchange."""

    def __init__(self, market_data_client: BaseMarketDataClient) -> None:
        self._client = market_data_client
        self._known_markets: dict[str, Market] = {}
        self._last_scan: datetime | None = None
        self._newly_listed: list[str] = []
        self._resolved: list[str] = []

    @property
    def known_markets(self) -> dict[str, Market]:
        return dict(self._known_markets)

    @property
    def active_markets(self) -> list[Market]:
        return [m for m in self._known_markets.values() if m.active]

    @property
    def last_scan_time(self) -> datetime | None:
        return self._last_scan

    @property
    def newly_listed(self) -> list[str]:
        return list(self._newly_listed)

    @property
    def resolved(self) -> list[str]:
        return list(self._resolved)

    async def scan(self) -> list[Market]:
        """Fetch all markets, detect additions and removals."""
        all_markets = await self._client.get_all_markets()
        now = utc_now()

        incoming_ids: set[str] = set()
        self._newly_listed = []
        self._resolved = []

        for market in all_markets:
            mid = market.market_id
            incoming_ids.add(mid)

            if mid not in self._known_markets:
                self._newly_listed.append(mid)
                logger.info(
                    "new_market_discovered",
                    market_id=mid,
                    question=market.question[:80],
                    exchange=market.exchange,
                )

            self._known_markets[mid] = market

        for mid in list(self._known_markets):
            if mid not in incoming_ids:
                existing = self._known_markets[mid]
                if existing.active:
                    existing.active = False
                    self._resolved.append(mid)
                    logger.info(
                        "market_resolved",
                        market_id=mid,
                        question=existing.question[:80],
                    )

        self._last_scan = now

        active = self.active_markets
        logger.info(
            "universe_scan_complete",
            total_known=len(self._known_markets),
            total_active=len(active),
            newly_listed=len(self._newly_listed),
            resolved=len(self._resolved),
        )
        return active

    def get_market(self, market_id: str) -> Market | None:
        return self._known_markets.get(market_id)
