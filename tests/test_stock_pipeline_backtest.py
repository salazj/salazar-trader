"""Tests for the full-pipeline stock backtester."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config.settings import Settings
from app.stocks.backtesting.engine import StockBacktestConfig
from app.stocks.backtesting.pipeline import StockPipelineBacktestEngine
from app.stocks.models import StockBar


def _settings(**overrides) -> Settings:
    base = dict(
        approved_stock_tickers="NVDA",
        stock_min_final_score=0.40,
        stock_require_stop_loss=True,
        stock_max_position_dollars=3000.0,
        stock_max_portfolio_dollars=100000.0,
        stock_max_open_positions=5,
        stock_max_trades_per_day=999,
        stock_max_orders_per_minute=999,
        stock_risk_per_trade_dollars=0.0,
    )
    base.update(overrides)
    return Settings(**base)


def _bar(sym, px, vol, t):
    return StockBar(
        symbol=sym, open=px, high=px * 1.002, low=px * 0.998,
        close=px, volume=vol, timestamp=t,
    )


def _mean_reversion_series() -> list[StockBar]:
    """Flat base, sharp oversold dip, then recovery back to VWAP."""
    t0 = datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc)
    bars: list[StockBar] = []
    i = 0
    for _ in range(40):  # flat → builds VWAP ~100
        bars.append(_bar("NVDA", 100.0, 10000, t0 + timedelta(minutes=i)))
        i += 1
    for px in (99.0, 97.5, 96.0, 94.5, 93.0, 92.0):  # sharp drop → RSI low
        bars.append(_bar("NVDA", px, 15000, t0 + timedelta(minutes=i)))
        i += 1
    for px in (93.0, 95.0, 97.0, 99.0, 100.0, 100.5, 101.0):  # recovery
        bars.append(_bar("NVDA", px, 12000, t0 + timedelta(minutes=i)))
        i += 1
    return bars


class TestPipelineBacktest:
    def test_runs_and_returns_wellformed_result(self) -> None:
        eng = StockPipelineBacktestEngine(
            _settings(), config=StockBacktestConfig(starting_cash=20000)
        )
        res = eng.run({"NVDA": _mean_reversion_series()})
        assert res.strategy == "full_pipeline"
        assert res.total_trades >= 0
        # One equity point per replayed bar.
        assert len(res.equity_curve) == len(_mean_reversion_series())
        assert res.ending_cash > 0

    def test_oversold_dip_produces_a_round_trip(self) -> None:
        eng = StockPipelineBacktestEngine(
            _settings(), config=StockBacktestConfig(starting_cash=20000)
        )
        res = eng.run({"NVDA": _mean_reversion_series()})
        # The oversold dip + recovery should trigger at least one closed trade.
        assert res.total_trades >= 1
        # Every closed trade carries its originating strategy + exit reason.
        for tr in res.trades:
            assert tr.strategy
            assert tr.rationale in {
                "stop", "target", "signal_exit", "forced_close",
            }
