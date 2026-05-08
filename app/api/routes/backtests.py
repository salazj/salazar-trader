"""Stock backtesting endpoints.

These endpoints let the GUI launch a backtest run synchronously and
list the most recent reports stored under ``reports/``. Long-lived
``reports/`` directory is mounted in the Docker compose file.
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.stocks.backtesting import (
    StockBacktestConfig,
    StockBacktestEngine,
    load_csv_bars,
    run_walk_forward,
)
from app.stocks.models import StockBar
from app.stocks.strategies import STRATEGY_REGISTRY

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


class BacktestRequest(BaseModel):
    strategy: str = Field(default="stock_momentum")
    tickers: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"])
    start: str | None = None
    end: str | None = None
    starting_cash: float = 10000.0
    max_position_dollars: float = 1000.0
    fee_per_trade: float = 0.0
    slippage_bps: float = 5.0
    data_dir: str | None = None
    walk_forward: bool = False
    train_size: int = 1000
    test_size: int = 250
    use_synthetic: bool = False


def _make_synthetic(tickers: list[str]) -> dict[str, list[StockBar]]:
    bars_by_symbol: dict[str, list[StockBar]] = {}
    base_ts = datetime(2024, 1, 2, 13, 30, tzinfo=timezone.utc)
    for sym in tickers:
        price = 100.0
        bars: list[StockBar] = []
        for i in range(2000):
            drift = math.sin(i / 60.0) * 0.5
            shock = math.cos(i * 0.13) * 0.6
            close = max(1.0, price + drift + shock)
            high = close + 0.4
            low = close - 0.4
            open_ = price
            vol = 100_000 + int(math.sin(i / 7.0) * 30_000)
            bars.append(
                StockBar(
                    symbol=sym,
                    timestamp=base_ts + timedelta(minutes=i),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=vol,
                )
            )
            price = close
        bars_by_symbol[sym] = bars
    return bars_by_symbol


def _load_real(req: BacktestRequest, tickers: list[str]) -> dict[str, list[StockBar]]:
    if not req.data_dir:
        raise HTTPException(
            status_code=400,
            detail="data_dir required (or set use_synthetic=true)",
        )
    data_dir = Path(req.data_dir)
    if not data_dir.exists():
        raise HTTPException(status_code=404, detail=f"data dir not found: {data_dir}")
    out: dict[str, list[StockBar]] = {}
    start = datetime.fromisoformat(req.start.replace("Z", "+00:00")) if req.start else None
    end = datetime.fromisoformat(req.end.replace("Z", "+00:00")) if req.end else None
    for sym in tickers:
        path = data_dir / f"{sym.upper()}.csv"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"missing CSV: {path}")
        bars = load_csv_bars(path, symbol=sym)
        if start is not None:
            bars = [b for b in bars if b.timestamp >= start]
        if end is not None:
            bars = [b for b in bars if b.timestamp <= end]
        out[sym.upper()] = bars
    return out


def _run_blocking(req: BacktestRequest) -> dict[str, Any]:
    tickers = [t.strip().upper() for t in req.tickers if t.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="tickers required")
    if req.strategy not in STRATEGY_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"unknown strategy: {req.strategy}",
        )

    bars_by_symbol = _make_synthetic(tickers) if req.use_synthetic else _load_real(req, tickers)

    config = StockBacktestConfig(
        starting_cash=req.starting_cash,
        max_position_dollars=req.max_position_dollars,
        fee_per_trade=req.fee_per_trade,
        slippage_bps=req.slippage_bps,
    )
    strategy_cls = STRATEGY_REGISTRY[req.strategy]

    if req.walk_forward:
        report = run_walk_forward(
            strategy_factory=strategy_cls,
            bars_by_symbol=bars_by_symbol,
            train_size=req.train_size,
            test_size=req.test_size,
            config=config,
        )
        payload = report.to_dict()
        kind = "walk_forward"
    else:
        engine = StockBacktestEngine(strategy_cls(), config=config)
        payload = engine.run(bars_by_symbol).summary_dict()
        kind = "single"

    settings = get_settings()
    out_dir = settings.reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname = f"backtest_{req.strategy}_{kind}_{stamp}.json"
    out_path = out_dir / fname
    out_path.write_text(json.dumps(payload, indent=2, default=str))

    return {"kind": kind, "report_path": str(out_path), "report": payload}


@router.get("")
async def list_backtest_reports(request: Request) -> list[dict[str, Any]]:
    settings = get_settings()
    out_dir = settings.reports_dir
    if not out_dir.exists():
        return []
    reports: list[dict[str, Any]] = []
    for p in sorted(out_dir.glob("backtest_*.json"), reverse=True)[:50]:
        try:
            stat = p.stat()
            reports.append(
                {
                    "filename": p.name,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
        except OSError:
            continue
    return reports


@router.post("/run")
async def run_backtest(request: Request, body: BacktestRequest) -> dict[str, Any]:
    return await asyncio.get_running_loop().run_in_executor(None, _run_blocking, body)
