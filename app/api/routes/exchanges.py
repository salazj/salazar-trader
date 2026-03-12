"""Exchange and broker listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import ExchangeInfo
from app.config.settings import get_settings
from app.monitoring import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["exchanges"])

_EXCHANGES: list[ExchangeInfo] = [
    ExchangeInfo(
        id="polymarket",
        name="Polymarket",
        asset_class="prediction_markets",
        config_fields=["polymarket_host", "chain_id", "private_key", "poly_api_key"],
    ),
    ExchangeInfo(
        id="kalshi",
        name="Kalshi",
        asset_class="prediction_markets",
        config_fields=["kalshi_api_key", "kalshi_private_key", "kalshi_demo_mode"],
    ),
    ExchangeInfo(
        id="alpaca",
        name="Alpaca",
        asset_class="equities",
        config_fields=["alpaca_api_key", "alpaca_secret_key", "alpaca_paper"],
    ),
]

_KALSHI_CATEGORIES_CACHE: list[str] = []


@router.get("/api/exchanges", response_model=list[ExchangeInfo])
async def list_exchanges() -> list[ExchangeInfo]:
    return _EXCHANGES


@router.get("/api/exchanges/categories")
async def list_categories(exchange: str = "kalshi") -> list[str]:
    """Return available market categories for the given exchange."""
    global _KALSHI_CATEGORIES_CACHE
    if exchange == "kalshi":
        if _KALSHI_CATEGORIES_CACHE:
            return _KALSHI_CATEGORIES_CACHE
        try:
            from app.exchanges.kalshi.market_data import KalshiMarketDataClient

            settings = get_settings()
            client = KalshiMarketDataClient(settings)
            try:
                categories = await client.get_available_categories()
                if categories:
                    _KALSHI_CATEGORIES_CACHE = categories
                return categories
            finally:
                await client.close()
        except Exception as e:
            logger.warning("categories_fetch_error", exchange=exchange, error=str(e))
            return [
                "climate and weather", "companies", "economics", "elections",
                "entertainment", "financials", "health", "politics",
                "science and technology", "social", "sports", "world",
            ]
    return []
