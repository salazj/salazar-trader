"""Risk state and control endpoints.

All bot access goes through BotManager — no direct bot access.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.schemas import RiskStateResponse

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("", response_model=RiskStateResponse)
async def get_risk_state(request: Request) -> RiskStateResponse:
    mgr = request.app.state.bot_manager
    return mgr.get_risk_state()


@router.get("/status", response_model=RiskStateResponse)
async def get_risk_status(request: Request) -> RiskStateResponse:
    """Alias for ``GET /api/risk`` matching the public Jetson API spec."""
    mgr = request.app.state.bot_manager
    return mgr.get_risk_state()


@router.post("/reset-breaker")
async def reset_circuit_breaker(request: Request) -> dict:
    mgr = request.app.state.bot_manager
    try:
        mgr.reset_circuit_breaker()
        return {"status": "ok", "message": "Circuit breaker reset"}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/reset-circuit-breaker")
async def reset_circuit_breaker_alias(request: Request) -> dict:
    """Alias matching the documented Jetson API surface."""
    mgr = request.app.state.bot_manager
    try:
        mgr.reset_circuit_breaker()
        return {"status": "ok", "message": "Circuit breaker reset"}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class EmergencyStopBody(BaseModel):
    confirm: bool = False
    reason: str = "Emergency stop via GUI"


@router.post("/emergency-stop")
async def emergency_stop(request: Request, body: EmergencyStopBody | None = None) -> dict:
    """Trip the emergency stop. The GUI MUST send ``{"confirm": true}``.

    The double-confirmation mirrors the live-trading gate pattern: a
    fat-finger click on the stop button shouldn't accidentally halt the
    whole bot mid-recovery.
    """
    if body is None or not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Emergency stop requires confirm=true in body",
        )
    mgr = request.app.state.bot_manager
    try:
        mgr.trip_emergency_stop(body.reason)
        return {"status": "ok", "message": "Emergency stop activated"}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
