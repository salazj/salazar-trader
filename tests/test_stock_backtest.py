"""Tests for the stock backtest engine and walk-forward harness."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.stocks.backtesting import (
    StockBacktestConfig,
    StockBacktestEngine,
    run_walk_forward,
    walk_forward_splits,
)
from app.stocks.models import StockBar
from app.stocks.strategies.momentum import StockMomentum
from app.stocks.strategies.mean_reversion import StockMeanReversion


def _make_bars(n: int = 1000, drift: float = 0.0, seed: int = 0) -> list[StockBar]:
    base = datetime(2024, 1, 2, 13, 30, tzinfo=timezone.utc)
    bars: list[StockBar] = []
    price = 100.0
    for i in range(n):
        wave = math.sin(i / 30.0) * 1.5
        price = max(1.0, price + drift + wave * 0.1)
        high = price + 0.5
        low = price - 0.5
        bars.append(
            StockBar(
                symbol="SPY",
                timestamp=base + timedelta(minutes=i),
                open=price,
                high=high,
                low=low,
                close=price,
                volume=200_000 + (10_000 if i % 13 == 0 else 0),
            )
        )
    return bars


class TestBacktestEngine:
    def test_run_produces_equity_curve(self) -> None:
        bars = _make_bars(500)
        engine = StockBacktestEngine(StockMomentum(), config=StockBacktestConfig())
        result = engine.run({"SPY": bars})
        assert result.tickers == ["SPY"]
        assert len(result.equity_curve) == 500
        assert result.starting_cash == pytest.approx(10000.0)
        # Cash conservation: ending_cash ~= starting_cash + sum(pnl)
        assert (
            abs((result.starting_cash + result.total_pnl) - result.ending_cash)
            < 1e-3
        )

    def test_signals_close_existing_long(self) -> None:
        bars = _make_bars(500)
        engine = StockBacktestEngine(StockMeanReversion(), config=StockBacktestConfig())
        result = engine.run({"SPY": bars})
        # Forced close at end means net qty == 0.
        opens = [t for t in result.trades if t.exit_time is None]
        assert opens == []


class TestWalkForward:
    def test_walk_forward_splits_chronological(self) -> None:
        splits = walk_forward_splits(n=1000, train_size=300, test_size=200)
        for prev, nxt in zip(splits, splits[1:]):
            # Test windows are strictly later than train windows.
            assert nxt.test_start >= prev.test_start
            assert nxt.train_start >= prev.train_start
            assert nxt.test_start > nxt.train_start

    def test_walk_forward_runs_over_all_splits(self) -> None:
        bars_by_symbol = {"SPY": _make_bars(800), "QQQ": _make_bars(800)}
        report = run_walk_forward(
            strategy_factory=StockMomentum,
            bars_by_symbol=bars_by_symbol,
            train_size=300,
            test_size=200,
        )
        assert report.splits >= 1
        assert report.train_size == 300
        assert report.test_size == 200
        assert isinstance(report.out_of_sample_pnl, float)
        assert all("split" in s for s in report.per_split)
