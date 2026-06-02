"""
FastAPI application factory for the $alazar-Trader platform.

Creates the app, mounts all REST and WebSocket routers, sets up CORS,
and configures the BotManager singleton on startup.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.bot_manager import BotManager
from app.api.log_broadcaster import log_broadcaster
from app.monitoring import setup_logging
from app.monitoring.logger import get_logger

logger = get_logger(__name__)

# Pre-built React bundle (committed by the frontend build step). When present,
# the API server doubles as the GUI host so the native install needs no nginx.
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle."""
    setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
    app.state.bot_manager = BotManager()
    logger.info("api_server_started")
    yield
    mgr: BotManager = app.state.bot_manager
    if mgr.is_running:
        await mgr.stop()
    logger.info("api_server_stopped")


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="$alazar-Trader API",
        version="3.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # REST routes
    from app.api.routes.status import router as status_router
    from app.api.routes.bot import router as bot_router
    from app.api.routes.config import router as config_router
    from app.api.routes.portfolio import router as portfolio_router
    from app.api.routes.risk import router as risk_router
    from app.api.routes.exchanges import router as exchanges_router
    from app.api.routes.strategies import router as strategies_router
    from app.api.routes.stock_decisions import router as decisions_router
    from app.api.routes.llm import router as llm_router
    from app.api.routes.backtests import router as backtests_router

    app.include_router(status_router)
    app.include_router(bot_router)
    app.include_router(config_router)
    app.include_router(portfolio_router)
    app.include_router(risk_router)
    app.include_router(exchanges_router)
    app.include_router(strategies_router)
    app.include_router(decisions_router)
    app.include_router(llm_router)
    app.include_router(backtests_router)

    # WebSocket routes
    from app.api.websocket.logs import router as ws_logs_router
    from app.api.websocket.status import router as ws_status_router
    from app.api.websocket.portfolio_ws import router as ws_portfolio_router

    app.include_router(ws_logs_router)
    app.include_router(ws_status_router)
    app.include_router(ws_portfolio_router)

    _mount_frontend(app)

    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the pre-built React SPA from STATIC_DIR, if it exists.

    REST (`/api/*`) and WebSocket (`/ws/*`) routes are registered before
    this and always take priority. Static asset files are served from
    `/assets`, and any other unmatched GET path falls back to `index.html`
    so client-side routing (react-router) works on deep links / refresh.
    """
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        logger.info("frontend_static_not_found", path=str(STATIC_DIR))
        return

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="assets",
        )

    @app.get("/")
    async def _serve_index() -> FileResponse:
        return FileResponse(str(index_file))

    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str) -> FileResponse:
        # Never intercept API/WS namespaces (already routed above, but guard
        # against unmatched sub-paths returning index.html instead of 404).
        if full_path.startswith(("api/", "ws/")):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")
        # Serve real files (favicon, etc.) when they exist; else SPA index.
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index_file))

    logger.info("frontend_static_mounted", path=str(STATIC_DIR))
