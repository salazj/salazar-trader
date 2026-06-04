"""Tests for stock risk controls."""

from __future__ import annotations

from app.config.settings import Settings
from app.data.models import PortfolioSnapshot
from app.stocks.risk import StockRiskManager


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        stock_max_position_dollars=1000.0,
        stock_max_portfolio_dollars=10000.0,
        stock_max_daily_loss_dollars=500.0,
        stock_max_open_positions=5,
        stock_max_orders_per_minute=10,
        allow_extended_hours=False,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_portfolio(**overrides) -> PortfolioSnapshot:
    defaults = dict(cash=10000.0, total_exposure=0.0)
    defaults.update(overrides)
    return PortfolioSnapshot(**defaults)


class TestStockRiskManager:
    def test_approve_normal_order(self):
        rm = StockRiskManager(_make_settings())
        result = rm.check_order(
            "AAPL", "buy", 150.0, 5, _make_portfolio(), stop_price=145.0,
        )
        assert result.approved is True

    def test_reject_exceeds_position_limit(self):
        rm = StockRiskManager(_make_settings(stock_max_position_dollars=500.0))
        result = rm.check_order(
            "AAPL", "buy", 150.0, 10, _make_portfolio(), stop_price=145.0,
        )
        assert result.approved is False
        assert "max position" in result.reason.lower()

    def test_reject_exceeds_portfolio_limit(self):
        rm = StockRiskManager(_make_settings(stock_max_portfolio_dollars=100.0))
        result = rm.check_order(
            "AAPL", "buy", 150.0, 5,
            _make_portfolio(total_exposure=50.0),
            stop_price=145.0,
        )
        assert result.approved is False
        assert "portfolio" in result.reason.lower()

    def test_reject_max_positions_reached(self):
        rm = StockRiskManager(_make_settings(stock_max_open_positions=2))
        from app.data.models import Position
        positions = [
            Position(token_id="a", exchange="alpaca"),
            Position(token_id="b", exchange="alpaca"),
        ]
        portfolio = PortfolioSnapshot(cash=10000.0, positions=positions)
        result = rm.check_order("AAPL", "buy", 150.0, 1, portfolio, stop_price=145.0)
        assert result.approved is False
        assert "positions" in result.reason.lower()

    def test_circuit_breaker_halts_trading(self):
        rm = StockRiskManager(_make_settings())
        rm.trip_circuit_breaker("Test halt")
        assert rm.is_halted is True
        result = rm.check_order(
            "AAPL", "buy", 150.0, 1, _make_portfolio(), stop_price=145.0,
        )
        assert result.approved is False

    def test_reset_circuit_breaker(self):
        rm = StockRiskManager(_make_settings())
        rm.trip_circuit_breaker("Test halt")
        rm.reset_circuit_breaker()
        assert rm.is_halted is False

    def test_daily_loss_triggers_breaker(self):
        rm = StockRiskManager(_make_settings(stock_max_daily_loss_dollars=100.0))
        rm._daily_pnl = -200.0
        result = rm.check_order(
            "AAPL", "buy", 150.0, 1, _make_portfolio(), stop_price=145.0,
        )
        assert result.approved is False
        assert rm.is_halted is True


class TestShortSellingGates:
    """Short entries (sell-to-open) must obey the same entry-only gates as
    longs, while a plain sell-to-close stays exempt (legacy long-only path)."""

    def test_short_entry_requires_stop_loss(self):
        rm = StockRiskManager(_make_settings(stock_require_stop_loss=True))
        # Sell flagged as an entry with no stop → rejected (mirror of a long).
        res = rm.check_order(
            "AAPL", "sell", 150.0, 1, _make_portfolio(),
            stop_price=None, is_entry=True,
        )
        assert res.approved is False
        assert "stop loss" in res.reason.lower()

    def test_short_close_does_not_require_stop(self):
        rm = StockRiskManager(_make_settings(stock_require_stop_loss=True))
        # Sell to *close* a long (is_entry defaults to False for SELL).
        res = rm.check_order("AAPL", "sell", 150.0, 1, _make_portfolio())
        assert res.approved is True

    def test_short_entry_counts_against_max_positions(self):
        from app.data.models import Position
        rm = StockRiskManager(_make_settings(stock_max_open_positions=2))
        positions = [
            Position(token_id="a", exchange="alpaca"),
            Position(token_id="b", exchange="alpaca"),
        ]
        portfolio = PortfolioSnapshot(cash=10000.0, positions=positions)
        res = rm.check_order(
            "AAPL", "sell", 150.0, 1, portfolio,
            stop_price=155.0, is_entry=True,
        )
        assert res.approved is False
        assert "positions" in res.reason.lower()

    def test_short_entry_consumes_daily_trade_budget(self):
        rm = StockRiskManager(_make_settings(stock_max_trades_per_day=1))
        ok = rm.check_order(
            "AAPL", "sell", 150.0, 1, _make_portfolio(),
            stop_price=155.0, is_entry=True, commit=True,
        )
        assert ok.approved is True
        assert rm.trades_today == 1
        blocked = rm.check_order(
            "MSFT", "sell", 150.0, 1, _make_portfolio(),
            stop_price=155.0, is_entry=True, commit=True,
        )
        assert blocked.approved is False
        assert "trades per day" in blocked.reason.lower()
