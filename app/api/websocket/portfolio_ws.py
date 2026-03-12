"""WebSocket endpoint for live portfolio updates."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/portfolio")
async def ws_portfolio(websocket: WebSocket) -> None:
    await websocket.accept()
    mgr = websocket.app.state.bot_manager

    try:
        while True:
            portfolio = await mgr.get_portfolio()
            orders = mgr.get_orders(limit=20)
            await websocket.send_json({
                "type": "portfolio",
                "portfolio": portfolio.model_dump(),
                "recent_orders": [o.model_dump() for o in orders],
            })
            await asyncio.sleep(2.0)
    except (WebSocketDisconnect, Exception):
        pass
