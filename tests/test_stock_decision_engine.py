"""Tests for the three-layer stock decision engine."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config.settings import Settings
from app.data.models import PortfolioSnapshot
from app.llm.schema import LLMSentiment, LLMVerdict, safe_default_verdict
from app.models.enums import OrderType, StockAction
from app.regime.detector import MarketRegime, RegimeReading
from app.stocks.decision import (
    DecisionStore,
    PerformanceTracker,
    StockDecisionAction,
    StockDecisionEngine,
)
from app.stocks.ml.predictor import StockMLPrediction
from app.stocks.models import StockFeatures, StockSignal
from app.stocks.risk import StockRiskManager
from app.utils.helpers import utc_now


def _settings(**overrides) -> Settings:
    defaults = dict(
        approved_stock_tickers="NVDA,SPY,AAPL",
        stock_max_position_dollars=200.0,
        stock_max_portfolio_dollars=1000.0,
        stock_max_daily_loss_dollars=50.0,
        stock_max_open_positions=3,
        stock_max_orders_per_minute=10,
        stock_max_trades_per_day=10,
        stock_require_stop_loss=True,
        stock_min_final_score=0.30,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _features(symbol: str = "NVDA", price: float = 100.0) -> StockFeatures:
    return StockFeatures(
        symbol=symbol,
        timestamp=utc_now(),
        last_price=price,
        ema_9=price,
        ema_21=price - 1,
        ema_50=price - 2,
        rsi_14=55.0,
        atr_14=1.5,
        vwap=price - 0.5,
    )


def _portfolio(cash: float = 5000.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(cash=cash, total_exposure=0.0)


def _signal(symbol: str = "NVDA", price: float = 100.0,
            action: StockAction = StockAction.BUY,
            stop: float | None = 95.0,
            confidence: float = 0.8) -> StockSignal:
    return StockSignal(
        strategy_name="stock_momentum",
        symbol=symbol,
        action=action,
        confidence=confidence,
        suggested_price=price,
        suggested_quantity=1,
        order_type=OrderType.LIMIT,
        stop_price=stop,
        rationale="test",
    )


def _bullish_regime() -> RegimeReading:
    return RegimeReading(
        regime=MarketRegime.TRENDING_BULLISH, confidence=0.8, score=0.05,
        timestamp=utc_now(),
    )


def _bearish_regime() -> RegimeReading:
    return RegimeReading(
        regime=MarketRegime.TRENDING_BEARISH, confidence=0.8, score=-0.05,
        timestamp=utc_now(),
    )


class TestStockDecisionEngine:
    def test_buy_passes_with_strong_l1(self) -> None:
        s = _settings()
        eng = StockDecisionEngine(s, risk_manager=StockRiskManager(s))
        trace = eng.evaluate(
            "NVDA",
            _features(),
            _portfolio(),
            l1_signal=_signal(confidence=0.85),
            ml_prediction=StockMLPrediction(
                ticker="NVDA", probability_up=0.65, confidence=0.3
            ),
            llm_verdict=LLMVerdict(ticker="NVDA", sentiment="bullish",
                                   relevance_score=0.5, confidence_adjustment=0.1),
            regime=_bullish_regime(),
        )
        assert trace.action == StockDecisionAction.BUY
        assert trace.risk_approved is True
        assert trace.blocked_reason is None
        assert trace.final_score > 0.3
        assert trace.stop_price == 95.0

    def test_no_l1_signal_blocks(self) -> None:
        s = _settings()
        eng = StockDecisionEngine(s, risk_manager=StockRiskManager(s))
        trace = eng.evaluate("NVDA", _features(), _portfolio())
        assert trace.action == StockDecisionAction.BLOCKED
        assert trace.blocked_reason is not None

    def test_llm_should_gate_trade_blocks(self) -> None:
        s = _settings()
        eng = StockDecisionEngine(s, risk_manager=StockRiskManager(s))
        verdict = LLMVerdict(
            ticker="NVDA", sentiment="bearish", relevance_score=0.9,
            confidence_adjustment=-0.2, should_gate_trade=True,
            risk_flags=["regulatory_risk"], summary="bad news",
        )
        trace = eng.evaluate(
            "NVDA", _features(), _portfolio(), l1_signal=_signal(),
            llm_verdict=verdict,
        )
        assert trace.action == StockDecisionAction.BLOCKED
        assert "LLM gated" in (trace.blocked_reason or "")
        assert trace.llm_should_gate is True

    def test_bearish_regime_blocks_long(self) -> None:
        s = _settings()
        eng = StockDecisionEngine(s, risk_manager=StockRiskManager(s))
        trace = eng.evaluate(
            "NVDA", _features(), _portfolio(),
            l1_signal=_signal(confidence=0.9),
            regime=_bearish_regime(),
        )
        assert trace.action == StockDecisionAction.BLOCKED
        assert "regime" in (trace.blocked_reason or "")

    def test_low_final_score_blocks(self) -> None:
        # Set the threshold high so the trade fails the gate.
        s = _settings(stock_min_final_score=0.95)
        eng = StockDecisionEngine(s, risk_manager=StockRiskManager(s))
        trace = eng.evaluate(
            "NVDA", _features(), _portfolio(),
            l1_signal=_signal(confidence=0.5),
            llm_verdict=safe_default_verdict("NVDA"),
        )
        assert trace.action == StockDecisionAction.BLOCKED
        assert "below threshold" in (trace.blocked_reason or "")

    def test_missing_stop_loss_blocked_by_risk(self) -> None:
        s = _settings(stock_require_stop_loss=True)
        eng = StockDecisionEngine(s, risk_manager=StockRiskManager(s))
        trace = eng.evaluate(
            "NVDA", _features(), _portfolio(),
            l1_signal=_signal(stop=None, confidence=0.9),
        )
        assert trace.action == StockDecisionAction.BLOCKED
        assert "stop" in (trace.blocked_reason or "").lower()

    def test_unknown_ticker_blocked_by_risk(self) -> None:
        s = _settings()
        eng = StockDecisionEngine(s, risk_manager=StockRiskManager(s))
        trace = eng.evaluate(
            "ZZZZ", _features("ZZZZ"), _portfolio(),
            l1_signal=_signal(symbol="ZZZZ", confidence=0.9),
        )
        assert trace.action == StockDecisionAction.BLOCKED
        assert "approved universe" in (trace.blocked_reason or "")

    def test_decision_trace_includes_scores_and_explanation(self) -> None:
        s = _settings()
        eng = StockDecisionEngine(s, risk_manager=StockRiskManager(s))
        trace = eng.evaluate(
            "NVDA", _features(), _portfolio(),
            l1_signal=_signal(confidence=0.7),
            ml_prediction=StockMLPrediction(
                ticker="NVDA", probability_up=0.6, confidence=0.2
            ),
            llm_verdict=LLMVerdict(ticker="NVDA", sentiment="bullish",
                                   relevance_score=0.4, confidence_adjustment=0.05),
        )
        d = trace.to_dict()
        assert "l1_score" in d and "l2_score" in d and "l3_score" in d
        assert "final_score" in d
        assert "explanation" in d
        assert d["timestamp"]


class TestDecisionStoreAndPerformance:
    def test_decision_store_recent_returns_newest_first(self) -> None:
        s = _settings()
        eng = StockDecisionEngine(s, risk_manager=StockRiskManager(s))
        store = DecisionStore(max_size=10)
        for i in range(3):
            t = eng.evaluate("NVDA", _features(price=100 + i), _portfolio())
            store.add(t)
        out = store.recent(limit=10)
        assert len(out) == 3

    def test_performance_tracker_rolls_up_metrics(self) -> None:
        tracker = PerformanceTracker()
        tracker.record_fill(symbol="NVDA", strategy="stock_momentum", pnl=10.0)
        tracker.record_fill(symbol="NVDA", strategy="stock_momentum", pnl=-4.0)
        tracker.record_fill(symbol="SPY", strategy="stock_pullback", pnl=6.0)
        s = tracker.summary()
        assert s.total_trades == 3
        assert s.wins == 2
        assert s.losses == 1
        assert s.total_pnl == 12.0
        assert s.win_rate > 0.6
        assert s.profit_factor > 0
        assert "stock_momentum" in s.by_strategy
