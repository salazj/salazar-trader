"""Exchange and broker listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import ExchangeInfo

router = APIRouter(tags=["exchanges"])

_EXCHANGES: list[ExchangeInfo] = [
    ExchangeInfo(
        id="polymarket",
        name="Polymarket",
        asset_class="prediction_markets",
        config_fields=["polymarket_host", "chain_id", "private_key", "poly_api_key"],
    ),
    ExchangeInfo(
        id="alpaca",
        name="Alpaca",
        asset_class="equities",
        config_fields=["alpaca_api_key", "alpaca_secret_key", "alpaca_paper"],
    ),
]

@router.get("/api/exchanges", response_model=list[ExchangeInfo])
async def list_exchanges() -> list[ExchangeInfo]:
    return _EXCHANGES


@router.get("/api/exchanges/categories")
async def list_categories(exchange: str = "polymarket") -> list[str]:
    """Return available market categories for the given exchange."""
    return []
