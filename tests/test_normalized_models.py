"""Tests for normalized cross-asset models (Phase 1)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import (
    AssetClass,
    Balance,
    Fill,
    Instrument,
    NormalizedPosition,
    OrderRequest,
    OrderType,
    PnLSnapshot,
    Quote,
    TimeInForce,
    TradeTick,
)


class TestEnums:
    def test_asset_class_values(self):
        assert AssetClass.PREDICTION_MARKETS.value == "prediction_markets"
        assert AssetClass.EQUITIES.value == "equities"

    def test_order_type_values(self):
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.MARKET.value == "market"
        assert OrderType.STOP.value == "stop"
        assert OrderType.STOP_LIMIT.value == "stop_limit"

    def test_time_in_force_values(self):
        assert TimeInForce.DAY.value == "day"
        assert TimeInForce.GTC.value == "gtc"


class TestInstrument:
    def test_create_equity_instrument(self):
        inst = Instrument(
            symbol="AAPL",
            asset_class=AssetClass.EQUITIES,
            exchange="alpaca",
            instrument_id="AAPL",
            name="Apple Inc.",
        )
        assert inst.symbol == "AAPL"
        assert inst.asset_class == AssetClass.EQUITIES
        assert inst.exchange == "alpaca"

    def test_create_prediction_market_instrument(self):
        inst = Instrument(
            symbol="will-x-happen",
            asset_class=AssetClass.PREDICTION_MARKETS,
            exchange="polymarket",
            instrument_id="KX-ABC-123",
        )
        assert inst.asset_class == AssetClass.PREDICTION_MARKETS

    def test_metadata_default_empty(self):
        inst = Instrument(
            symbol="TEST",
            asset_class=AssetClass.EQUITIES,
            exchange="test",
            instrument_id="TEST",
        )
        assert inst.metadata == {}


class TestQuote:
    def test_mid_price(self):
        q = Quote(
            instrument_id="AAPL",
            asset_class=AssetClass.EQUITIES,
            exchange="alpaca",
            bid=149.5,
            ask=150.5,
            timestamp=datetime.now(timezone.utc),
        )
        assert q.mid == 150.0

    def test_spread(self):
        q = Quote(
            instrument_id="AAPL",
            asset_class=AssetClass.EQUITIES,
            exchange="alpaca",
            bid=149.5,
            ask=150.5,
            timestamp=datetime.now(timezone.utc),
        )
        assert q.spread == 1.0

    def test_mid_falls_back_to_last(self):
        q = Quote(
            instrument_id="AAPL",
            asset_class=AssetClass.EQUITIES,
            exchange="alpaca",
            last=150.0,
            timestamp=datetime.now(timezone.utc),
        )
        assert q.mid == 150.0


class TestOrderRequest:
    def test_create_order_request(self):
        req = OrderRequest(
            instrument_id="AAPL",
            asset_class=AssetClass.EQUITIES,
            exchange="alpaca",
            side="buy",
            quantity=10,
            price=150.0,
            order_type=OrderType.LIMIT,
        )
        assert req.side == "buy"
        assert req.quantity == 10
        assert req.order_type == OrderType.LIMIT


class TestFill:
    def test_create_fill(self):
        f = Fill(
            instrument_id="AAPL",
            order_id="order-1",
            side="buy",
            price=150.0,
            quantity=10,
            commission=0.50,
            timestamp=datetime.now(timezone.utc),
        )
        assert f.commission == 0.50


class TestPortfolioModels:
    def test_normalized_position(self):
        pos = NormalizedPosition(
            instrument_id="AAPL",
            asset_class=AssetClass.EQUITIES,
            exchange="alpaca",
            symbol="AAPL",
            quantity=10,
            avg_cost=150.0,
            market_value=1520.0,
            unrealized_pnl=20.0,
        )
        assert pos.unrealized_pnl == 20.0

    def test_balance(self):
        bal = Balance(cash=10000.0, buying_power=20000.0, portfolio_value=30000.0)
        assert bal.currency == "USD"

    def test_pnl_snapshot(self):
        snap = PnLSnapshot(
            timestamp=datetime.now(timezone.utc),
            realized_pnl=100.0,
            unrealized_pnl=-50.0,
            daily_pnl=50.0,
            total_equity=10050.0,
        )
        assert snap.daily_pnl == 50.0
