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

    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        if self._auth is None:
            return {}
        query = str(httpx.QueryParams(params or {}))
        path_with_query = f"{path}?{query}" if query else path
        full_path = "/trade-api/v2" + path_with_query
        return self._auth.sign_request(method, full_path)

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = self._auth_headers("GET", path, params=params)
        resp = await self._client.get(path, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ── Markets ───────────────────────────────────────────────────────

    async def get_markets(self, cursor: str = "") -> tuple[list[Market], str]:
        params: dict[str, Any] = {"limit": 100, "status": "open"}
        if cursor:
            params["cursor"] = cursor

        data = await self._get("/markets", params=params)
        next_cursor = data.get("cursor", "")
        raw_markets = data.get("markets", [])

        markets = [normalize_market(m) for m in raw_markets]
        logger.info(
            "kalshi_fetched_markets",
            count=len(markets),
            next_cursor=next_cursor[:20] if next_cursor else "",
        )
        return markets, next_cursor

    async def get_all_markets(self, max_pages: int = 50) -> list[Market]:
        all_markets: list[Market] = []
        cursor = ""
        for page_num in range(max_pages):
            page, cursor = await self.get_markets(cursor)
            all_markets.extend(page)
            if not cursor:
                break
            logger.debug("kalshi_paging_markets", page=page_num + 1, fetched=len(all_markets))
        logger.info("kalshi_fetched_all_markets", total=len(all_markets))
        return all_markets

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
