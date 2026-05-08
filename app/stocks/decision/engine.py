"""Three-layer decision engine for stocks.

Combines:

* **L1** — deterministic technical strategy signals
* **L2** — tabular ML probability of upward move
* **L3** — local LLM sentiment + risk verdict

Final score::

    final_score =
        L1_score * L1_WEIGHT +
        L2_score * L2_WEIGHT +
        L3_score * L3_WEIGHT

Plus the LLM's bounded confidence_adjustment (clipped to ±0.25).

The engine never bypasses risk controls. Every evaluation produces a
:class:`StockDecisionTrace` with full inputs, scores, the risk-manager
result and the final action — even when the trade is blocked.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum

from app.config.settings import Settings
from app.data.models import PortfolioSnapshot
from app.llm.schema import LLMSentiment, LLMVerdict, safe_default_verdict
from app.regime.detector import MarketRegime, RegimeReading
from app.stocks.ml.predictor import StockMLPrediction
from app.stocks.models import StockFeatures, StockSignal
from app.stocks.risk import StockRiskCheckResult, StockRiskManager
from app.utils.helpers import utc_now


class StockDecisionAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    BLOCKED = "blocked"


@dataclass
class StockDecisionTrace:
    ticker: str
    timestamp: datetime
    strategy: str
    l1_score: float
    l2_score: float
    l3_score: float
    final_score: float
    action: StockDecisionAction
    risk_checks: list[str] = field(default_factory=list)
    risk_approved: bool = False
    blocked_reason: str | None = None
    explanation: str = ""
    weights: dict[str, float] = field(default_factory=dict)
    suggested_price: float | None = None
    suggested_quantity: int | None = None
    stop_price: float | None = None
    regime: str | None = None
    llm_sentiment: str | None = None
    llm_summary: str | None = None
    llm_should_gate: bool = False
    ml_probability_up: float | None = None
    ml_confidence: float | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        d["action"] = self.action.value
        return d


# ── Helpers ────────────────────────────────────────────────────────────


def _l1_score(signal: StockSignal | None) -> tuple[float, int]:
    """Return signed score (-1..+1) and direction (+1/-1/0) from a signal."""
    if signal is None:
        return 0.0, 0
    direction = 1 if signal.action.value.upper() == "BUY" else (
        -1 if signal.action.value.upper() == "SELL" else 0
    )
    return float(signal.confidence) * direction, direction


def _l2_score(prediction: StockMLPrediction | None) -> float:
    if prediction is None:
        return 0.0
    return (prediction.probability_up - 0.5) * 2.0  # -1..+1


def _l3_score(verdict: LLMVerdict | None) -> float:
    if verdict is None:
        return 0.0
    sign = {
        LLMSentiment.BULLISH: 1.0,
        LLMSentiment.NEUTRAL: 0.0,
        LLMSentiment.BEARISH: -1.0,
    }[verdict.sentiment]
    return sign * float(verdict.relevance_score)


# ── Engine ─────────────────────────────────────────────────────────────


class StockDecisionEngine:
    """Combine L1/L2/L3 layers and run risk checks.

    The engine is deliberately stateless apart from the configurable
    weights. Per-tick state (rolling LLM history, regime, etc.) is
    passed in by the caller (typically the trading bot main loop).
    """

    def __init__(
        self,
        settings: Settings,
        risk_manager: StockRiskManager | None = None,
        weights: tuple[float, float, float] | None = None,
    ) -> None:
        self._settings = settings
        self._risk = risk_manager
        w = weights or (
            settings.stock_l1_weight,
            settings.stock_l2_weight,
            settings.stock_l3_weight,
        )
        total = sum(w) or 1.0
        self._w_l1 = w[0] / total
        self._w_l2 = w[1] / total
        self._w_l3 = w[2] / total
        self._min_score = settings.stock_min_final_score

    @property
    def weights(self) -> dict[str, float]:
        return {"l1": self._w_l1, "l2": self._w_l2, "l3": self._w_l3}

    def evaluate(
        self,
        ticker: str,
        features: StockFeatures,
        portfolio: PortfolioSnapshot,
        *,
        l1_signal: StockSignal | None = None,
        ml_prediction: StockMLPrediction | None = None,
        llm_verdict: LLMVerdict | None = None,
        regime: RegimeReading | None = None,
        broker=None,
    ) -> StockDecisionTrace:
        """Evaluate one candidate and return a fully-populated trace."""
        verdict = llm_verdict or safe_default_verdict(ticker)
        l1, direction = _l1_score(l1_signal)
        l2 = _l2_score(ml_prediction)
        l3 = _l3_score(verdict)

        adj = float(verdict.confidence_adjustment)
        final = (
            l1 * self._w_l1 + l2 * self._w_l2 + l3 * self._w_l3 + adj
        )
        final = max(-1.0, min(1.0, final))

        explanation_parts: list[str] = []
        explanation_parts.append(
            f"L1({l1_signal.strategy_name if l1_signal else 'none'})={l1:+.2f}"
        )
        explanation_parts.append(f"L2={l2:+.2f}")
        explanation_parts.append(f"L3({verdict.sentiment.value})={l3:+.2f}")
        if adj:
            explanation_parts.append(f"adj={adj:+.2f}")
        if regime is not None:
            explanation_parts.append(f"regime={regime.regime.value}")

        # ── Block reasons (deterministic) ──────────────────────────────

        block_reason: str | None = None
        action: StockDecisionAction = StockDecisionAction.HOLD

        if l1_signal is None:
            block_reason = "no L1 strategy signal"
        elif verdict.should_gate_trade:
            block_reason = f"LLM gated trade: {verdict.summary[:120]}"
        elif regime is not None and direction > 0 and not regime.allows_long:
            block_reason = (
                f"regime {regime.regime.value} disallows long entries"
            )
        elif abs(final) < self._min_score:
            block_reason = (
                f"final_score {final:+.2f} below threshold {self._min_score:.2f}"
            )

        if block_reason is None and l1_signal is not None:
            action = (
                StockDecisionAction.BUY
                if direction > 0
                else StockDecisionAction.SELL
                if direction < 0
                else StockDecisionAction.HOLD
            )

        # ── Risk gate ─────────────────────────────────────────────────

        risk_result: StockRiskCheckResult | None = None
        if (
            self._risk is not None
            and l1_signal is not None
            and direction != 0
            and block_reason is None
        ):
            price = l1_signal.suggested_price or features.last_price
            qty = (
                l1_signal.suggested_quantity
                or self._compute_quantity(price, portfolio)
            )
            side = "BUY" if direction > 0 else "SELL"
            risk_result = self._risk.check_order(
                symbol=ticker,
                side=side,
                price=price,
                quantity=qty,
                portfolio=portfolio,
                broker=broker,
                stop_price=l1_signal.stop_price,
                bar_timestamp=features.timestamp,
            )
            if not risk_result.approved:
                block_reason = f"risk: {risk_result.reason}"

        risk_checks = list(risk_result.checks) if risk_result else []
        risk_approved = bool(risk_result and risk_result.approved)

        if block_reason is not None:
            action = StockDecisionAction.BLOCKED

        trace = StockDecisionTrace(
            ticker=ticker.upper(),
            timestamp=utc_now(),
            strategy=l1_signal.strategy_name if l1_signal else "none",
            l1_score=l1,
            l2_score=l2,
            l3_score=l3,
            final_score=final,
            action=action,
            risk_checks=risk_checks,
            risk_approved=risk_approved,
            blocked_reason=block_reason,
            explanation=" | ".join(explanation_parts),
            weights=dict(self.weights),
            suggested_price=l1_signal.suggested_price if l1_signal else None,
            suggested_quantity=l1_signal.suggested_quantity if l1_signal else None,
            stop_price=l1_signal.stop_price if l1_signal else None,
            regime=regime.regime.value if regime else None,
            llm_sentiment=verdict.sentiment.value,
            llm_summary=verdict.summary,
            llm_should_gate=verdict.should_gate_trade,
            ml_probability_up=ml_prediction.probability_up if ml_prediction else None,
            ml_confidence=ml_prediction.confidence if ml_prediction else None,
            metadata={
                "ml_version": ml_prediction.model_version if ml_prediction else None,
                "llm_relevance": verdict.relevance_score,
                "llm_risk_flags": list(verdict.risk_flags),
            },
        )
        return trace

    def _compute_quantity(self, price: float, portfolio: PortfolioSnapshot) -> int:
        if price <= 0:
            return 0
        max_dollars = min(
            self._settings.stock_max_position_dollars,
            max(portfolio.cash * 0.25, 0),
        )
        return max(1, int(max_dollars / price)) if max_dollars > price else 0


# ── In-memory decision store ──────────────────────────────────────────


class DecisionStore:
    """Bounded ring buffer of recent ``StockDecisionTrace`` objects.

    The control API exposes this via ``GET /api/decisions/recent`` so the
    GUI can show the live decision stream without needing a database.
    """

    def __init__(self, max_size: int = 500) -> None:
        self._buffer: deque[StockDecisionTrace] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def add(self, trace: StockDecisionTrace) -> None:
        with self._lock:
            self._buffer.append(trace)

    def recent(self, limit: int = 50) -> list[StockDecisionTrace]:
        with self._lock:
            data = list(self._buffer)
        return data[-limit:][::-1]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


# ── Performance tracker ───────────────────────────────────────────────


@dataclass
class PerformanceSummary:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    by_strategy: dict[str, dict] = field(default_factory=dict)


class PerformanceTracker:
    """Lightweight, in-memory PnL/win-rate tracker for the dashboard."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fills: list[dict] = []
        self._equity_curve: list[float] = []
        self._cumulative_pnl: float = 0.0

    def record_fill(
        self,
        *,
        symbol: str,
        strategy: str,
        pnl: float,
        timestamp: datetime | None = None,
    ) -> None:
        with self._lock:
            self._fills.append(
                {
                    "symbol": symbol.upper(),
                    "strategy": strategy,
                    "pnl": float(pnl),
                    "timestamp": (timestamp or utc_now()).isoformat(),
                }
            )
            self._cumulative_pnl += float(pnl)
            self._equity_curve.append(self._cumulative_pnl)

    def summary(self) -> PerformanceSummary:
        with self._lock:
            fills = list(self._fills)
            curve = list(self._equity_curve)

        s = PerformanceSummary()
        s.total_trades = len(fills)
        if not fills:
            return s

        wins = [f for f in fills if f["pnl"] > 0]
        losses = [f for f in fills if f["pnl"] < 0]
        s.wins = len(wins)
        s.losses = len(losses)
        s.total_pnl = sum(f["pnl"] for f in fills)
        s.win_rate = s.wins / s.total_trades if s.total_trades else 0.0
        s.average_win = sum(f["pnl"] for f in wins) / s.wins if s.wins else 0.0
        s.average_loss = sum(f["pnl"] for f in losses) / s.losses if s.losses else 0.0
        s.gross_profit = sum(f["pnl"] for f in wins)
        s.gross_loss = -sum(f["pnl"] for f in losses)
        s.profit_factor = s.gross_profit / s.gross_loss if s.gross_loss else 0.0

        if curve:
            peak = curve[0]
            mdd = 0.0
            for v in curve:
                peak = max(peak, v)
                dd = peak - v
                if dd > mdd:
                    mdd = dd
            s.max_drawdown = mdd

        if len(curve) > 2:
            diffs = [curve[i] - curve[i - 1] for i in range(1, len(curve))]
            mean = sum(diffs) / len(diffs)
            var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
            std = var ** 0.5
            if std > 0:
                s.sharpe = (mean / std) * (252 ** 0.5)

        by_strategy: dict[str, dict] = {}
        for f in fills:
            entry = by_strategy.setdefault(
                f["strategy"], {"trades": 0, "pnl": 0.0, "wins": 0}
            )
            entry["trades"] += 1
            entry["pnl"] += f["pnl"]
            if f["pnl"] > 0:
                entry["wins"] += 1
        for v in by_strategy.values():
            v["win_rate"] = v["wins"] / v["trades"] if v["trades"] else 0.0
        s.by_strategy = by_strategy
        return s
