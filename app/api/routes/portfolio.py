"""Portfolio, positions, orders, fills, and PnL history endpoints.

All data retrieval goes through BotManager — no direct bot access.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas import (
    FillItem,
    OrderItem,
    PnLHistoryItem,
    PortfolioResponse,
    PositionItem,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioResponse)
async def get_portfolio(request: Request) -> PortfolioResponse:
    mgr = request.app.state.bot_manager
    return await mgr.get_portfolio()


@router.get("/positions", response_model=list[PositionItem])
async def get_positions(request: Request) -> list[PositionItem]:
    mgr = request.app.state.bot_manager
    portfolio = await mgr.get_portfolio()
    return portfolio.positions


@router.get("/orders", response_model=list[OrderItem])
async def get_orders(request: Request, limit: int = 50) -> list[OrderItem]:
    mgr = request.app.state.bot_manager
    return mgr.get_orders(limit=limit)


@router.get("/fills", response_model=list[FillItem])
async def get_fills(request: Request, limit: int = 50) -> list[FillItem]:
    mgr = request.app.state.bot_manager
    return await mgr.get_fills(limit=limit)


@router.get("/pnl-history", response_model=list[PnLHistoryItem])
async def get_pnl_history(request: Request, limit: int = 200) -> list[PnLHistoryItem]:
    mgr = request.app.state.bot_manager
    return await mgr.get_pnl_history(limit=limit)
