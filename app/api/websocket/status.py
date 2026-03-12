"""WebSocket endpoint for live bot status updates."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket) -> None:
    await websocket.accept()
    mgr = websocket.app.state.bot_manager

    try:
        while True:
            status = mgr.get_status()
            risk = mgr.get_risk_state()
            await websocket.send_json({
                "type": "status",
                "bot": status.model_dump(),
                "risk": risk.model_dump(),
            })
            await asyncio.sleep(1.5)
    except (WebSocketDisconnect, Exception):
        pass
