"""Stock decision trace endpoints.

These endpoints expose the running decision engine's state to the GUI:
recent decision traces, the current market regime, and a rolling
performance summary.

If the bot is not running yet, endpoints return empty payloads — they
never fail.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api", tags=["stock-decisions"])


@router.get("/decisions/recent")
async def get_recent_decisions(
    request: Request, limit: int = Query(default=50, ge=1, le=500)
) -> list[dict[str, Any]]:
    mgr = request.app.state.bot_manager
    return mgr.get_recent_decisions(limit=limit)


@router.get("/regime/current")
async def get_current_regime(request: Request) -> dict[str, Any]:
    mgr = request.app.state.bot_manager
    return mgr.get_current_regime() or {"regime": "unknown", "confidence": 0.0}


@router.get("/performance/summary")
async def get_performance_summary(request: Request) -> dict[str, Any]:
    mgr = request.app.state.bot_manager
    return mgr.get_performance_summary()
