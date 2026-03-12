"""
Kalshi market data client implementing the BaseMarketDataClient interface.

Uses Kalshi REST API v2 for:
  - market metadata (list, search, single market)
  - orderbook snapshots
  - event grouping
  - trade history
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config.settings import Settings
from app.data.models import Market
from app.exchanges.base import BaseMarketDataClient
from app.exchanges.kalshi.auth import KalshiAuth
from app.exchanges.kalshi.normalizer import (
    cents_to_decimal,
    normalize_market,
    normalize_orderbook,
    normalize_trade,
)
from app.monitoring import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3

DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"


class KalshiMarketDataClient(BaseMarketDataClient):
    """REST client for Kalshi market data."""

    def __init__(self, settings: Settings) -> None:
        base_url = DEMO_BASE_URL if settings.kalshi_demo_mode else settings.kalshi_base_url
        self._base_url = base_url
        self._auth: KalshiAuth | None = None

        if settings.kalshi_api_key and (settings.kalshi_private_key or settings.kalshi_private_key_path):
            self._auth = KalshiAuth(
                settings.kalshi_api_key,
                settings.kalshi_private_key_path,
                private_key_pem=settings.kalshi_private_key,
            )

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if self._auth is None:
            return {}
        full_path = "/trade-api/v2" + path
        return self._auth.sign_request(method, full_path)

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = self._auth_headers("GET", path)
        resp = await self._client.get(path, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ── Markets ───────────────────────────────────────────────────────

    async def get_markets(self, cursor: str = "", limit: int = 1000) -> tuple[list[Market], str]:
        params: dict[str, Any] = {"limit": limit, "status": "open"}
        if cursor:
            params["cursor"] = cursor

        data = await self._get("/markets", params=params)
        next_cursor = data.get("cursor", "")
        raw_markets = data.get("markets", [])

        markets = [normalize_market(m) for m in raw_markets if not _is_parlay(m)]
        logger.info(
            "kalshi_fetched_markets",
            count=len(markets),
            raw=len(raw_markets),
            next_cursor=next_cursor[:20] if next_cursor else "",
        )
        return markets, next_cursor

    async def get_all_markets(self, max_markets: int = 5000) -> list[Market]:
        all_markets: list[Market] = []
        cursor = ""
        max_raw_pages = 100
        consecutive_empty = 0
        for page_num in range(max_raw_pages):
            page, cursor = await self.get_markets(cursor)
            all_markets.extend(page)
            if not cursor:
                break
            if len(all_markets) >= max_markets:
                break
            if page:
                consecutive_empty = 0
            else:
                consecutive_empty += 1
            if len(all_markets) >= 500 and consecutive_empty >= 20:
                logger.info("kalshi_paging_stopped_early", reason="consecutive_empty",
                            pages=page_num + 1, fetched=len(all_markets))
                break
            logger.debug("kalshi_paging_markets", page=page_num + 1, fetched=len(all_markets))

        cat_map = await self.get_event_categories()
        if cat_map:
            for market in all_markets:
                event_ticker = (market.exchange_data or {}).get("event_ticker", "")
                cat = cat_map.get(event_ticker, "")
                if cat:
                    market.category = cat
                    market.exchange_data["category"] = cat

        logger.info("kalshi_fetched_all_markets", total=len(all_markets))
        return all_markets

    async def get_event_categories(self) -> dict[str, str]:
        """Fetch events and return a mapping of event_ticker -> category."""
        cat_map: dict[str, str] = {}
        cursor = ""
        try:
            for _ in range(50):
                params: dict[str, Any] = {"limit": 200, "status": "open"}
                if cursor:
                    params["cursor"] = cursor
                data = await self._get("/events", params=params)
                for event in data.get("events", []):
                    ticker = event.get("event_ticker", "")
                    category = event.get("category", "")
                    if ticker and category:
                        cat_map[ticker] = category.lower()
                cursor = data.get("cursor", "")
                if not cursor:
                    break
            logger.info("kalshi_event_categories_fetched", count=len(cat_map))
        except Exception as e:
            logger.warning("kalshi_event_categories_error", error=str(e))
        return cat_map

    async def get_available_categories(self) -> list[str]:
        """Return sorted list of unique market categories from Kalshi events."""
        cat_map = await self.get_event_categories()
        return sorted(set(cat_map.values()))

    async def get_market(self, market_id: str) -> Market | None:
        try:
            data = await self._get(f"/markets/{market_id}")
            raw = data.get("market", data)
            return normalize_market(raw)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug("kalshi_market_not_found", market_id=market_id)
                return None
            raise

    # ── Events ────────────────────────────────────────────────────────

    async def get_event(self, event_ticker: str) -> dict[str, Any] | None:
        """Fetch a Kalshi event (group of related markets)."""
        try:
            data = await self._get(f"/events/{event_ticker}")
            return data.get("event", data)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_event_markets(self, event_ticker: str) -> list[Market]:
        """Fetch all markets belonging to a Kalshi event."""
        params: dict[str, Any] = {"event_ticker": event_ticker, "limit": 100}
        data = await self._get("/markets", params=params)
        raw_markets = data.get("markets", [])
        return [normalize_market(m) for m in raw_markets]

    # ── Orderbook ─────────────────────────────────────────────────────

    async def get_orderbook(self, instrument_id: str) -> dict[str, Any]:
        ticker = instrument_id.rstrip("-no")
        data = await self._get(f"/markets/{ticker}/orderbook")
        raw = data.get("orderbook", data)
        book = normalize_orderbook(ticker, raw)
        return book.model_dump()

    async def get_midpoint(self, instrument_id: str) -> float | None:
        try:
            ticker = instrument_id.rstrip("-no")
            data = await self._get(f"/markets/{ticker}")
            raw = data.get("market", data)
            yes_price = raw.get("yes_price", raw.get("last_price"))
            if yes_price is not None:
                return cents_to_decimal(yes_price)
            return None
        except Exception:
            return None

    # ── Trade history ─────────────────────────────────────────────────

    async def get_trades(
        self,
        ticker: str,
        limit: int = 100,
        cursor: str = "",
    ) -> tuple[list[dict[str, Any]], str]:
        """Fetch recent trades for a market.

        Returns (trades, next_cursor).
        """
        params: dict[str, Any] = {"ticker": ticker, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await self._get("/markets/trades", params=params)
            raw_trades = data.get("trades", [])
            next_cursor = data.get("cursor", "")
            trades = [normalize_trade(t).model_dump() for t in raw_trades]
            return trades, next_cursor
        except Exception as e:
            logger.warning("kalshi_trades_error", ticker=ticker, error=str(e))
            return [], ""

    # ── Series / lookup ───────────────────────────────────────────────

    async def search_markets(self, query: str, limit: int = 20) -> list[Market]:
        """Search Kalshi markets by keyword."""
        try:
            data = await self._get("/markets", params={"limit": limit, "status": "open"})
            raw = data.get("markets", [])
            q = query.lower()
            matches = [m for m in raw if q in (m.get("title", "") + m.get("subtitle", "")).lower()]
            return [normalize_market(m) for m in matches[:limit]]
        except Exception as e:
            logger.warning("kalshi_search_error", query=query, error=str(e))
            return []


_PARLAY_PREFIXES = ("KXMVE", "KXCROSSCATEGORY", "KXPARLAY")


def _is_parlay(raw: dict[str, Any]) -> bool:
    """Return True for synthetic cross-category / parlay combo markets."""
    ticker = raw.get("ticker", "")
    return any(ticker.startswith(p) for p in _PARLAY_PREFIXES)
