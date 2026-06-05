"""Local LLM status / test endpoints (Jetson Orin Nano)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.llm import LocalLLMService
from app.llm.service import LLMRequest

router = APIRouter(prefix="/api/llm", tags=["llm"])


class LLMTestRequest(BaseModel):
    ticker: str = Field(default="NVDA")
    technical_context: str = Field(default="EMA9>EMA21, RSI 58, vol surge 1.4x")
    news_context: str = Field(default="No fresh material news")


@router.get("/status")
async def llm_status(request: Request) -> dict[str, Any]:
    mgr = request.app.state.bot_manager
    info = mgr.get_llm_status()
    if info is None:
        settings = get_settings()
        # Build a transient service to report configuration even when the
        # bot is not running — useful for the GUI.
        svc = LocalLLMService(settings)
        info = svc.status()
    return info


@router.post("/test")
async def llm_test(request: Request, body: LLMTestRequest) -> dict[str, Any]:
    settings = get_settings()
    svc = LocalLLMService(settings)
    if svc.provider.name == "none":
        return {
            "status": "disabled",
            "message": "LOCAL_LLM_PROVIDER=none — set llama_cpp or ollama to enable",
            "verdict": None,
        }
    try:
        verdict = await svc.evaluate(
            LLMRequest(
                ticker=body.ticker,
                technical_context=body.technical_context,
                news_context=body.news_context,
            )
        )
        return {
            "status": "ok",
            "verdict": verdict.model_dump(),
            "service": svc.status(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
