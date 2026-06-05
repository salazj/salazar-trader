"""Tests for the new Jetson stock risk gates.

Existing coverage in ``test_stock_risk.py`` validates the legacy
position/portfolio/circuit-breaker gates. This module adds checks for
the new Jetson-specific gates: ticker allow-list, stale-data,
require-stop-loss, max-trades-per-day, and revenge-trade guard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config.settings import Settings
from app.data.models import PortfolioSnapshot
from app.stocks.risk import StockRiskManager


def _settings(**overrides) -> Settings:
    defaults = dict(
        approved_stock_tickers="NVDA,SPY",
        stock_max_position_dollars=200.0,
        stock_max_portfolio_dollars=1000.0,
        stock_max_daily_loss_dollars=50.0,
        stock_max_open_positions=3,
        stock_max_orders_per_minute=10,
        stock_max_trades_per_day=2,
        stock_require_stop_loss=True,
        stock_max_consecutive_losses_per_symbol=2,
        stock_max_bar_age_seconds=120,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _portfolio(**overrides) -> PortfolioSnapshot:
    defaults = dict(cash=10000.0, total_exposure=0.0)
    defaults.update(overrides)
    return PortfolioSnapshot(**defaults)


class TestTickerAllowList:
    def test_unapproved_ticker_blocked(self) -> None:
        rm = StockRiskManager(_settings())
        result = rm.check_order(
            "ZZZZ", "buy", 100.0, 1, _portfolio(), stop_price=95.0,
        )
        assert result.approved is False
        assert "approved universe" in result.reason

    def test_approved_ticker_allowed(self) -> None:
        rm = StockRiskManager(_settings())
        result = rm.check_order(
            "NVDA", "buy", 100.0, 1, _portfolio(), stop_price=95.0,
        )
        assert result.approved is True


class TestStopLossRequirement:
    def test_buy_without_stop_blocked(self) -> None:
        rm = StockRiskManager(_settings())
        result = rm.check_order("NVDA", "buy", 100.0, 1, _portfolio(), stop_price=None)
        assert result.approved is False
        assert "stop loss" in result.reason.lower()

    def test_sell_without_stop_allowed(self) -> None:
        rm = StockRiskManager(_settings())
        result = rm.check_order("NVDA", "sell", 100.0, 1, _portfolio(), stop_price=None)
        assert result.approved is True

    def test_stop_loss_disabled_passes(self) -> None:
        rm = StockRiskManager(_settings(stock_require_stop_loss=False))
        result = rm.check_order("NVDA", "buy", 100.0, 1, _portfolio(), stop_price=None)
        assert result.approved is True


class TestStaleDataRejection:
    def test_old_bar_rejected(self) -> None:
        rm = StockRiskManager(_settings(stock_max_bar_age_seconds=10))
        old = datetime.now(timezone.utc) - timedelta(seconds=300)
        result = rm.check_order(
            "NVDA", "buy", 100.0, 1, _portfolio(),
            stop_price=95.0, bar_timestamp=old,
        )
        assert result.approved is False
        assert "stale" in result.reason.lower()

    def test_fresh_bar_accepted(self) -> None:
        rm = StockRiskManager(_settings())
        fresh = datetime.now(timezone.utc)
        result = rm.check_order(
            "NVDA", "buy", 100.0, 1, _portfolio(),
            stop_price=95.0, bar_timestamp=fresh,
        )
        assert result.approved is True


class TestMaxTradesPerDay:
    def test_max_trades_blocks_after_threshold(self) -> None:
        rm = StockRiskManager(_settings(stock_max_trades_per_day=2))
        for _ in range(2):
            r = rm.check_order(
                "NVDA", "buy", 100.0, 1, _portfolio(), stop_price=95.0,
            )
            assert r.approved is True
        third = rm.check_order(
            "NVDA", "buy", 100.0, 1, _portfolio(), stop_price=95.0,
        )
        assert third.approved is False
        assert "trades per day" in third.reason.lower()


class TestRevengeTradeGuard:
    def test_consecutive_losses_block_same_symbol(self) -> None:
        rm = StockRiskManager(_settings(stock_max_consecutive_losses_per_symbol=2))
        rm.record_fill(-10.0, symbol="NVDA")
        rm.record_fill(-15.0, symbol="NVDA")
        result = rm.check_order(
            "NVDA", "buy", 100.0, 1, _portfolio(), stop_price=95.0,
        )
        assert result.approved is False
        assert "revenge" in result.reason.lower()

    def test_other_symbol_unaffected(self) -> None:
        rm = StockRiskManager(_settings(stock_max_consecutive_losses_per_symbol=2))
        rm.record_fill(-10.0, symbol="NVDA")
        rm.record_fill(-15.0, symbol="NVDA")
        result = rm.check_order(
            "SPY", "buy", 100.0, 1, _portfolio(), stop_price=95.0,
        )
        assert result.approved is True

    def test_winning_trade_resets_streak(self) -> None:
        rm = StockRiskManager(_settings(stock_max_consecutive_losses_per_symbol=2))
        rm.record_fill(-10.0, symbol="NVDA")
        rm.record_fill(-15.0, symbol="NVDA")
        rm.record_fill(20.0, symbol="NVDA")
        result = rm.check_order(
            "NVDA", "buy", 100.0, 1, _portfolio(), stop_price=95.0,
        )
        assert result.approved is True


class TestStatusDict:
    def test_status_dict_includes_universe_and_limits(self) -> None:
        rm = StockRiskManager(_settings())
        d = rm.status_dict()
        assert "approved_universe" in d
        assert "max_trades_per_day" in d
        assert "halted" in d
        assert d["max_trades_per_day"] == 2
