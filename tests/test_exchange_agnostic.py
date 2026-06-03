"""
Exchange-agnostic tests proving the same strategy, decision engine, risk manager,
feature engine, orderbook manager, and portfolio tracker all work on normalized
Polymarket data.

This file contains no exchange-specific imports — every test uses only the
normalized domain models.
"""

from __future__ import annotations

import pytest

from app.data.features import FeatureEngine
from app.data.models import (
    Market,
    MarketFeatures,
    MarketToken,
    Order,
    OrderbookSnapshot,
    OrderStatus,
    OutcomeSide,
    PortfolioSnapshot,
    Position,
    PriceLevel,
    Side,
    Signal,
    SignalAction,
    Trade,
)
from app.data.orderbook import OrderbookManager
from app.decision.engine import DecisionEngine, signal_to_normalized
from app.decision.ensemble import EnsembleConfig
from app.decision.signals import (
    DecisionTrace,
    IntelligenceLayer,
    NormalizedSignal,
    TradeCandidate,
)
from app.portfolio.tracker import PortfolioTracker
from app.risk.manager import RiskManager


# ═══════════════════════════════════════════════════════════════════════
# Fixtures — normalized Polymarket data
# ═══════════════════════════════════════════════════════════════════════

EXCHANGES = ["polymarket"]


def _make_market(exchange: str = "polymarket") -> Market:
    return Market(
        condition_id="0xabc123",
        market_id="0xabc123",
        question="Will BTC exceed $100k?",
        slug="btc-100k",
        tokens=[
            MarketToken(token_id="tok-yes-123", instrument_id="tok-yes-123", outcome="Yes"),
            MarketToken(token_id="tok-no-456", instrument_id="tok-no-456", outcome="No"),
        ],
        exchange="polymarket",
    )


def _make_book(exchange: str) -> OrderbookSnapshot:
    market = _make_market(exchange)
    iid = market.tokens[0].instrument_id
    return OrderbookSnapshot(
        market_id=market.market_id,
        instrument_id=iid,
        token_id=iid,
        exchange=exchange,
        bids=[
            PriceLevel(price=0.55, size=100.0),
            PriceLevel(price=0.54, size=80.0),
            PriceLevel(price=0.53, size=60.0),
        ],
        asks=[
            PriceLevel(price=0.58, size=90.0),
            PriceLevel(price=0.59, size=70.0),
            PriceLevel(price=0.60, size=50.0),
        ],
    )


def _make_features(exchange: str) -> MarketFeatures:
    market = _make_market(exchange)
    iid = market.tokens[0].instrument_id
    return MarketFeatures(
        market_id=market.market_id,
        instrument_id=iid,
        token_id=iid,
        exchange=exchange,
        best_bid=0.55,
        best_ask=0.58,
        spread=0.03,
        mid_price=0.565,
        microprice=0.566,
        orderbook_imbalance=0.05,
        bid_depth_5c=240.0,
        ask_depth_5c=210.0,
        recent_trade_flow=1.5,
        volatility_1m=0.005,
        momentum_1m=0.002,
        momentum_5m=0.008,
        momentum_15m=0.015,
        trade_count_1m=8,
        seconds_since_last_update=1.0,
    )


def _make_signal(exchange: str, action: SignalAction = SignalAction.BUY_YES) -> Signal:
    market = _make_market(exchange)
    iid = market.tokens[0].instrument_id
    return Signal(
        strategy_name="passive_market_maker",
        market_id=market.market_id,
        token_id=iid,
        instrument_id=iid,
        exchange=exchange,
        action=action,
        confidence=0.72,
        suggested_price=0.55,
        suggested_size=2.0,
        rationale="spread opportunity detected",
    )


def _make_order(exchange: str) -> Order:
    market = _make_market(exchange)
    iid = market.tokens[0].instrument_id
    return Order(
        order_id="test-order-1",
        market_id=market.market_id,
        token_id=iid,
        instrument_id=iid,
        exchange=exchange,
        side=Side.BUY,
        price=0.55,
        size=2.0,
    )


# ═══════════════════════════════════════════════════════════════════════
# Feature Engine — same computation from both exchanges' normalized data
# ═══════════════════════════════════════════════════════════════════════


class TestFeatureEngineCrossExchange:
    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_feature_engine_produces_features(self, exchange):
        market = _make_market(exchange)
        iid = market.tokens[0].instrument_id
        engine = FeatureEngine(market.market_id, instrument_id=iid, exchange=exchange)

        book = _make_book(exchange)
        features = engine.compute(book)

        assert features.instrument_id == iid
        assert features.exchange == exchange
        assert features.best_bid == 0.55
        assert features.best_ask == 0.58
        assert features.spread is not None
        assert features.mid_price is not None

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_feature_engine_adds_trade(self, exchange):
        market = _make_market(exchange)
        iid = market.tokens[0].instrument_id
        engine = FeatureEngine(market.market_id, instrument_id=iid, exchange=exchange)

        trade = Trade(
            market_id=market.market_id,
            instrument_id=iid,
            exchange=exchange,
            price=0.56,
            size=10.0,
            side=Side.BUY,
        )
        engine.add_trade(trade)

        book = _make_book(exchange)
        features = engine.compute(book)
        assert features.last_trade_price == 0.56


# ═══════════════════════════════════════════════════════════════════════
# Orderbook Manager — exchange-agnostic keying
# ═══════════════════════════════════════════════════════════════════════


class TestOrderbookManagerCrossExchange:
    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_snapshot_and_retrieve(self, exchange):
        mgr = OrderbookManager()
        market = _make_market(exchange)
        iid = market.tokens[0].instrument_id

        mgr.apply_snapshot(
            market_id=market.market_id,
            instrument_id=iid,
            bids=[{"price": 0.55, "size": 100}],
            asks=[{"price": 0.58, "size": 90}],
        )

        snap = mgr.get_snapshot(iid)
        assert snap is not None
        assert snap.best_bid == 0.55
        assert snap.best_ask == 0.58

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_delta_update(self, exchange):
        mgr = OrderbookManager()
        market = _make_market(exchange)
        iid = market.tokens[0].instrument_id

        mgr.apply_snapshot(
            market_id=market.market_id,
            instrument_id=iid,
            bids=[{"price": 0.55, "size": 100}],
            asks=[{"price": 0.58, "size": 90}],
        )

        mgr.apply_delta(
            instrument_id=iid,
            bid_updates=[{"price": 0.56, "size": 50}],
        )

        snap = mgr.get_snapshot(iid)
        assert snap is not None
        assert len(snap.bids) == 2

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_list_format_levels(self, exchange):
        """Prove that [price, size] format works the same as dict format."""
        mgr = OrderbookManager()
        market = _make_market(exchange)
        iid = market.tokens[0].instrument_id

        mgr.apply_snapshot(
            market_id=market.market_id,
            instrument_id=iid,
            bids=[[0.55, 100]],
            asks=[[0.58, 90]],
        )

        snap = mgr.get_snapshot(iid)
        assert snap is not None
        assert snap.best_bid == 0.55

    def test_multiple_instruments_coexist(self):
        mgr = OrderbookManager()
        market = _make_market()
        for token in market.tokens:
            iid = token.instrument_id
            mgr.apply_snapshot(
                market_id=market.market_id,
                instrument_id=iid,
                bids=[{"price": 0.50, "size": 50}],
                asks=[{"price": 0.60, "size": 60}],
            )

        all_ids = mgr.get_all_instrument_ids()
        assert len(all_ids) == 2


# ═══════════════════════════════════════════════════════════════════════
# Signal normalization — same conversion path for both exchanges
# ═══════════════════════════════════════════════════════════════════════


class TestSignalNormalizationCrossExchange:
    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_signal_to_normalized_preserves_exchange(self, exchange):
        signal = _make_signal(exchange)
        norm = signal_to_normalized(signal, IntelligenceLayer.RULES)

        assert norm.instrument_id == signal.instrument_id
        assert norm.exchange == exchange
        assert norm.token_id == signal.instrument_id
        assert norm.direction == 1

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_sell_signal_direction(self, exchange):
        signal = _make_signal(exchange, SignalAction.SELL_YES)
        norm = signal_to_normalized(signal, IntelligenceLayer.RULES)
        assert norm.direction == -1


# ═══════════════════════════════════════════════════════════════════════
# Decision Engine — same ensemble logic for both exchanges
# ═══════════════════════════════════════════════════════════════════════


class TestDecisionEngineCrossExchange:
    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_evaluate_produces_candidate_and_trace(self, exchange):
        engine = DecisionEngine(EnsembleConfig(min_confidence=0.1, min_layers_agree=1, min_evidence_signals=1))
        features = _make_features(exchange)
        signal = _make_signal(exchange)
        norm = signal_to_normalized(signal, IntelligenceLayer.RULES)

        candidate, trace = engine.evaluate(
            market_id=features.market_id,
            instrument_id=features.instrument_id,
            exchange=exchange,
            features=features,
            portfolio=PortfolioSnapshot(cash=100.0),
            l1_signals=[norm],
        )

        assert isinstance(candidate, TradeCandidate)
        assert isinstance(trace, DecisionTrace)
        assert trace.instrument_id == features.instrument_id
        assert trace.exchange == exchange
        assert candidate.instrument_id == features.instrument_id
        assert candidate.exchange == exchange

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_hold_on_no_signals(self, exchange):
        engine = DecisionEngine()
        features = _make_features(exchange)

        candidate, trace = engine.evaluate(
            market_id=features.market_id,
            instrument_id=features.instrument_id,
            exchange=exchange,
            features=features,
            portfolio=PortfolioSnapshot(cash=100.0),
        )

        assert candidate.action == SignalAction.HOLD


# ═══════════════════════════════════════════════════════════════════════
# Risk Manager — exchange-independent checks on normalized data
# ═══════════════════════════════════════════════════════════════════════


class TestRiskManagerCrossExchange:
    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_approved_order(self, exchange, settings):
        risk = RiskManager(settings)
        features = _make_features(exchange)
        portfolio = PortfolioSnapshot(cash=100.0)

        result = risk.check_order(
            instrument_id=features.instrument_id,
            side=Side.BUY,
            price=0.55,
            size=1.0,
            features=features,
            portfolio=portfolio,
        )
        assert result.approved is True

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_rejected_insufficient_cash(self, exchange, settings):
        risk = RiskManager(settings)
        features = _make_features(exchange)
        portfolio = PortfolioSnapshot(cash=0.50)

        result = risk.check_order(
            instrument_id=features.instrument_id,
            side=Side.BUY,
            price=0.55,
            size=10.0,
            features=features,
            portfolio=portfolio,
        )
        assert result.approved is False
        assert "insufficient_cash" in result.reason

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_market_exposure_check_with_existing_position(self, exchange, settings):
        risk = RiskManager(settings)
        features = _make_features(exchange)
        iid = features.instrument_id

        portfolio = PortfolioSnapshot(
            cash=100.0,
            positions=[
                Position(
                    market_id=features.market_id,
                    instrument_id=iid,
                    exchange=exchange,
                    token_side=OutcomeSide.YES,
                    size=9.0,
                    avg_entry_price=0.50,
                ),
            ],
            total_exposure=4.5,
        )

        result = risk.check_order(
            instrument_id=iid,
            side=Side.BUY,
            price=0.55,
            size=5.0,
            features=features,
            portfolio=portfolio,
        )
        assert result.approved is False
        assert "market_exposure" in result.reason


# ═══════════════════════════════════════════════════════════════════════
# Portfolio Tracker — exchange-agnostic position management
# ═══════════════════════════════════════════════════════════════════════


class TestPortfolioTrackerCrossExchange:
    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_buy_creates_position(self, exchange, settings):
        tracker = PortfolioTracker(settings, starting_cash=100.0)
        order = _make_order(exchange)

        realized = tracker.on_fill(order, fill_price=0.55, fill_size=2.0)
        assert realized == 0.0

        pos = tracker.get_position(order.instrument_id)
        assert pos is not None
        assert pos.instrument_id == order.instrument_id
        assert pos.exchange == exchange
        assert pos.size == 2.0
        assert pos.avg_entry_price == 0.55

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_sell_realizes_pnl(self, exchange, settings):
        tracker = PortfolioTracker(settings, starting_cash=100.0)
        buy_order = _make_order(exchange)
        tracker.on_fill(buy_order, fill_price=0.50, fill_size=2.0)

        sell_order = Order(
            order_id="sell-1",
            market_id=buy_order.market_id,
            instrument_id=buy_order.instrument_id,
            exchange=exchange,
            side=Side.SELL,
            price=0.60,
            size=2.0,
        )
        realized = tracker.on_fill(sell_order, fill_price=0.60, fill_size=2.0)
        assert realized == pytest.approx(0.20, abs=0.01)

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_restore_position(self, exchange, settings):
        tracker = PortfolioTracker(settings, starting_cash=100.0)
        market = _make_market(exchange)
        iid = market.tokens[0].instrument_id

        tracker.restore_position(
            instrument_id=iid,
            market_id=market.market_id,
            exchange=exchange,
            token_side=OutcomeSide.YES,
            size=5.0,
            avg_entry_price=0.50,
            realized_pnl=0.10,
        )

        pos = tracker.get_position(iid)
        assert pos is not None
        assert pos.exchange == exchange
        assert pos.size == 5.0

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_mark_to_market(self, exchange, settings):
        tracker = PortfolioTracker(settings, starting_cash=100.0)
        order = _make_order(exchange)
        tracker.on_fill(order, fill_price=0.50, fill_size=5.0)

        tracker.mark_to_market(order.instrument_id, 0.60)

        pos = tracker.get_position(order.instrument_id)
        assert pos is not None
        assert pos.unrealized_pnl == pytest.approx(0.50, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════
# Strategy — same strategy produces valid signals from either exchange
# ═══════════════════════════════════════════════════════════════════════


class TestStrategyCrossExchange:
    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_passive_market_maker(self, exchange, settings):
        from app.strategies.passive_market_maker import PassiveMarketMaker

        strategy = PassiveMarketMaker(settings)
        features = _make_features(exchange)
        portfolio = PortfolioSnapshot(cash=100.0)

        signal = strategy.generate_signal(features, portfolio)
        if signal is not None:
            assert signal.instrument_id == features.instrument_id
            assert signal.exchange == exchange
            assert signal.action in {
                SignalAction.BUY_YES,
                SignalAction.SELL_YES,
                SignalAction.HOLD,
                SignalAction.BUY_NO,
                SignalAction.SELL_NO,
            }

    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_momentum_scalper(self, exchange, settings):
        from app.strategies.momentum_scalper import MomentumScalper

        strategy = MomentumScalper(settings)
        features = _make_features(exchange)
        portfolio = PortfolioSnapshot(cash=100.0)

        signal = strategy.generate_signal(features, portfolio)
        if signal is not None:
            assert signal.instrument_id == features.instrument_id
            assert signal.exchange == exchange


# ═══════════════════════════════════════════════════════════════════════
# End-to-end: normalized data → features → signal → decision → risk
# ═══════════════════════════════════════════════════════════════════════


class TestEndToEndCrossExchange:
    @pytest.mark.parametrize("exchange", EXCHANGES)
    def test_full_pipeline(self, exchange, settings):
        """Prove the entire pipeline works exchange-agnostically."""
        market = _make_market(exchange)
        iid = market.tokens[0].instrument_id

        # 1. Orderbook manager accepts normalized data
        book_mgr = OrderbookManager()
        book_mgr.apply_snapshot(
            market_id=market.market_id,
            instrument_id=iid,
            bids=[{"price": 0.55, "size": 100}, {"price": 0.54, "size": 80}],
            asks=[{"price": 0.58, "size": 90}, {"price": 0.59, "size": 70}],
        )

        # 2. Feature engine computes features
        fe = FeatureEngine(market.market_id, instrument_id=iid, exchange=exchange)
        book = book_mgr.get_snapshot(iid)
        assert book is not None
        features = fe.compute(book)
        assert features.exchange == exchange
        assert features.instrument_id == iid

        # 3. Strategy generates signal
        from app.strategies.passive_market_maker import PassiveMarketMaker
        strategy = PassiveMarketMaker(settings)
        portfolio = PortfolioSnapshot(cash=100.0)
        signal = strategy.generate_signal(features, portfolio)

        # 4. Decision engine evaluates
        de = DecisionEngine(EnsembleConfig(min_confidence=0.1, min_layers_agree=1, min_evidence_signals=1))
        l1 = []
        if signal is not None:
            l1.append(signal_to_normalized(signal, IntelligenceLayer.RULES))

        candidate, trace = de.evaluate(
            market_id=market.market_id,
            instrument_id=iid,
            exchange=exchange,
            features=features,
            portfolio=portfolio,
            l1_signals=l1,
        )
        assert trace.exchange == exchange

        # 5. Risk manager checks
        risk = RiskManager(settings)
        if not candidate.blocked and candidate.action != SignalAction.HOLD:
            risk_result = risk.check_order(
                instrument_id=iid,
                side=Side.BUY,
                price=candidate.suggested_price or 0.55,
                size=candidate.suggested_size or 1.0,
                features=features,
                portfolio=portfolio,
            )
            assert isinstance(risk_result.approved, bool)

        # 6. Portfolio tracker can process fills
        tracker = PortfolioTracker(settings, starting_cash=100.0)
        order = Order(
            order_id="e2e-1",
            market_id=market.market_id,
            instrument_id=iid,
            exchange=exchange,
            side=Side.BUY,
            price=0.55,
            size=2.0,
        )
        tracker.on_fill(order, fill_price=0.55, fill_size=2.0)
        pos = tracker.get_position(iid)
        assert pos is not None
        assert pos.exchange == exchange
