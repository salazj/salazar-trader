"""WebSocket endpoint for live portfolio updates."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/portfolio")
async def ws_portfolio(websocket: WebSocket) -> None:
    await websocket.accept()
    mgr = websocket.app.state.bot_manager

    exchange_order_counter = 0

    try:
        while True:
            portfolio = await mgr.get_portfolio()
            orders = mgr.get_orders(limit=20)

            payload: dict = {
                "type": "portfolio",
                "portfolio": portfolio.model_dump(),
                "recent_orders": [o.model_dump() for o in orders],
            }

            exchange_order_counter += 1
            if exchange_order_counter >= 5:
                exchange_order_counter = 0
                try:
                    exchange_orders = await mgr.get_exchange_orders()
                    payload["exchange_orders"] = [o.model_dump() for o in exchange_orders]
                except Exception:
                    pass

            await websocket.send_json(payload)
            await asyncio.sleep(2.0)
    except (WebSocketDisconnect, Exception):
        pass
