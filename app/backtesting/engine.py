"""
Backtesting Engine

Replays saved market states through a strategy to evaluate performance
without risking real capital.

IMPORTANT ASSUMPTIONS AND LIMITATIONS:
- Fill simulation is probabilistic (default 50%) and uses a seeded RNG for
  reproducibility. Real fill rates depend on queue position, market conditions,
  and order size relative to book depth — none of which are modeled here.
- Slippage is applied as a fixed basis-point offset. Real slippage is
  non-linear and depends on order size vs available liquidity.
- Fees are modeled as a flat percentage of notional. Real fee schedules
  may differ (maker/taker, volume tiers).
- The engine processes snapshots sequentially with no notion of time-priority
  or partial fills. A single snapshot = one potential trade.
- Market impact is not modeled. In reality, our own orders would move prices.
- Results should be treated as indicative, not predictive of live performance.

Supports:
- Strategy selection and parameter configuration
- Simulated order placement with configurable fill assumptions
- Fee and slippage modeling
- Comprehensive output metrics
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.config.settings import Settings
from app.data.models import (
    BacktestResult,
    MarketFeatures,
    Order,
    OrderStatus,
    PortfolioSnapshot,
    Side,
    Signal,
    SignalAction,
)
from app.decision.engine import DecisionEngine, signal_to_normalized
from app.decision.ensemble import DecisionMode, EnsembleConfig
from app.decision.signals import IntelligenceLayer, NormalizedSignal
from app.monitoring import get_logger
from app.nlp.pipeline import nlp_signal_to_layered
from app.nlp.signals import NlpSignal
from app.portfolio.tracker import PortfolioTracker
from app.risk.manager import RiskManager
from app.strategies.base import BaseStrategy

logger = get_logger(__name__)

# Maps signal actions to order sides
_ACTION_TO_SIDE = {
    SignalAction.BUY_YES: Side.BUY,
    SignalAction.BUY_NO: Side.BUY,
    SignalAction.SELL_YES: Side.SELL,
    SignalAction.SELL_NO: Side.SELL,
}


class BacktestConfig:
    """Configuration for a single backtest run."""

    def __init__(
        self,
        fee_rate: float = 0.02,
        slippage_bps: float = 10.0,
        fill_probability: float = 0.5,
        starting_cash: float = 100.0,
    ) -> None:
        self.fee_rate = fee_rate
        self.slippage_bps = slippage_bps / 10000.0
        self.fill_probability = fill_probability
        self.starting_cash = starting_cash


class BacktestEngine:
    """
    Drives a strategy through historical market feature snapshots and
    simulates trading with realistic assumptions about fills, fees, and slippage.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        settings: Settings,
        config: BacktestConfig | None = None,
    ) -> None:
        self._strategy = strategy
        self._settings = settings
        self._config = config or BacktestConfig()
        self._portfolio = PortfolioTracker(settings, starting_cash=self._config.starting_cash)
        self._risk = RiskManager(settings)

        self._trades: list[dict] = []
        self._equity_curve: list[float] = []
        self._pnl_by_market: dict[str, float] = {}
        # Fixed seed for reproducibility; override via config if needed
        self._rng = np.random.default_rng(seed=42)

    def run(self, feature_snapshots: list[MarketFeatures]) -> BacktestResult:
        """
        Execute the backtest over a time-ordered list of feature snapshots.

        Each snapshot represents market state at a point in time. The strategy
        sees each snapshot sequentially and may generate signals.
        """
        if not feature_snapshots:
            raise ValueError("No feature snapshots provided for backtest")

        logger.info(
            "backtest_started",
            strategy=self._strategy.name,
            snapshots=len(feature_snapshots),
            starting_cash=self._config.starting_cash,
        )

        start_time = feature_snapshots[0].timestamp
        end_time = feature_snapshots[-1].timestamp

        for i, features in enumerate(feature_snapshots):
            portfolio_snap = self._portfolio.get_snapshot()
            signal = self._strategy.generate_signal(features, portfolio_snap)

            if signal is not None and signal.action != SignalAction.HOLD:
                self._simulate_execution(signal, features, portfolio_snap)

            # Record equity
            snap = self._portfolio.get_snapshot()
            equity = snap.cash + sum(p.market_value for p in snap.positions)
            self._equity_curve.append(equity)

            # Mark existing positions to market
            iid = features.instrument_id or features.token_id
            if features.mid_price is not None:
                self._portfolio.mark_to_market(iid, features.mid_price)

        # Compute result metrics
        result = self._compute_results(start_time, end_time)

        logger.info(
            "backtest_completed",
            total_return=f"{result.total_return:.2%}",
            max_drawdown=f"{result.max_drawdown:.2%}",
            win_rate=f"{result.win_rate:.2%}",
            total_trades=result.total_trades,
        )

        return result

    def _simulate_execution(
        self,
        signal: Signal,
        features: MarketFeatures,
        portfolio: PortfolioSnapshot,
    ) -> None:
        """Simulate order placement and possible fill."""
        side = _ACTION_TO_SIDE.get(signal.action)
        if side is None:
            return

        price = signal.suggested_price or (features.mid_price or 0.5)
        size = signal.suggested_size or self._settings.default_order_size

        iid = signal.instrument_id or signal.token_id
        risk_result = self._risk.check_order(
            instrument_id=iid,
            side=side,
            price=price,
            size=size,
            features=features,
            portfolio=portfolio,
        )
        if not risk_result.approved:
            return

        if self._rng.random() > self._config.fill_probability:
            return

        if side == Side.BUY:
            fill_price = price * (1 + self._config.slippage_bps)
        else:
            fill_price = price * (1 - self._config.slippage_bps)

        fee = size * fill_price * self._config.fee_rate

        order = Order(
            order_id=f"BT-{len(self._trades):06d}",
            market_id=signal.market_id,
            token_id=iid,
            instrument_id=iid,
            exchange=signal.exchange,
            side=side,
            price=fill_price,
            size=size,
            filled_size=size,
            status=OrderStatus.FILLED,
        )

        # on_fill returns realized PnL for sells (computed from avg_entry BEFORE reduction)
        realized = self._portfolio.on_fill(order, fill_price, size)
        # Deduct fees from cash separately for backtest simulation
        with self._portfolio._lock:
            self._portfolio._cash -= fee

        pnl = realized - fee if side == Side.SELL else -fee
        if side == Side.SELL:
            self._risk.record_fill(pnl)

        self._pnl_by_market.setdefault(signal.market_id, 0.0)
        self._pnl_by_market[signal.market_id] += pnl

        self._trades.append({
            "timestamp": features.timestamp.isoformat() if hasattr(features.timestamp, "isoformat") else str(features.timestamp),
            "market_id": signal.market_id,
            "instrument_id": iid,
            "exchange": signal.exchange,
            "side": side.value,
            "price": fill_price,
            "size": size,
            "fee": fee,
            "pnl": pnl,
        })

    def _compute_results(self, start_time: datetime, end_time: datetime) -> BacktestResult:
        equity = np.array(self._equity_curve) if self._equity_curve else np.array([self._config.starting_cash])
        initial = self._config.starting_cash

        total_return = (equity[-1] - initial) / initial if initial > 0 else 0.0
        max_drawdown = self._compute_max_drawdown(equity)
        sharpe = self._compute_sharpe(equity)

        wins = sum(1 for t in self._trades if t["pnl"] > 0)
        total_trades = len(self._trades)
        win_rate = wins / total_trades if total_trades > 0 else 0.0

        avg_duration = 0.0

        exposure_sum = sum(self._equity_curve) - len(self._equity_curve) * initial
        exposure_util = abs(exposure_sum / (len(self._equity_curve) * initial)) if self._equity_curve else 0.0

        exchange_str = self._trades[0].get("exchange", "") if self._trades else ""
        return BacktestResult(
            strategy_name=self._strategy.name,
            start_time=start_time,
            end_time=end_time,
            exchange=exchange_str,
            total_return=total_return,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            total_trades=total_trades,
            avg_trade_duration_seconds=avg_duration,
            sharpe_ratio=sharpe,
            exposure_utilization=exposure_util,
            pnl_by_market=dict(self._pnl_by_market),
            parameters={
                "fee_rate": self._config.fee_rate,
                "slippage_bps": self._config.slippage_bps * 10000,
                "fill_probability": self._config.fill_probability,
                "starting_cash": self._config.starting_cash,
            },
        )

    @staticmethod
    def _compute_max_drawdown(equity: np.ndarray) -> float:
        if len(equity) < 2:
            return 0.0
        running_max = np.maximum.accumulate(equity)
        drawdowns = (equity - running_max) / running_max
        return float(np.min(drawdowns))

    @staticmethod
    def _compute_sharpe(equity: np.ndarray, periods_per_year: float = 365 * 24) -> float | None:
        """Simplified Sharpe from equity curve returns."""
        if len(equity) < 3:
            return None
        returns = np.diff(equity) / equity[:-1]
        if np.std(returns) == 0:
            return None
        return float(np.mean(returns) / np.std(returns) * math.sqrt(periods_per_year))

    def save_report(self, result: BacktestResult, output_dir: Path) -> None:
        """Save backtest results as JSON and trades as CSV."""
        output_dir.mkdir(parents=True, exist_ok=True)

        json_path = output_dir / f"backtest_{result.strategy_name}.json"
        with open(json_path, "w") as f:
            json.dump(result.model_dump(), f, indent=2, default=str)

        csv_path = output_dir / f"backtest_{result.strategy_name}_trades.csv"
        if self._trades:
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self._trades[0].keys())
                writer.writeheader()
                writer.writerows(self._trades)

        logger.info("backtest_report_saved", json_path=str(json_path), csv_path=str(csv_path))


# ---------------------------------------------------------------------------
# Multi-layer backtest engine
# ---------------------------------------------------------------------------


class MultiLayerBacktestEngine:
    """Backtest engine that evaluates all three intelligence layers
    through the DecisionEngine ensemble, tracking per-layer contributions.

    Supports selectively enabling/disabling layers to compare:
    L1-only, L2-only, L1+L2, all-layers, etc.
    """

    def __init__(
        self,
        settings: Settings,
        l1_strategies: list[BaseStrategy] | None = None,
        ml_strategy: BaseStrategy | None = None,
        nlp_signals_by_step: list[list[NlpSignal]] | None = None,
        decision_engine: DecisionEngine | None = None,
        config: BacktestConfig | None = None,
        active_layers: list[str] | None = None,
    ) -> None:
        self._settings = settings
        self._config = config or BacktestConfig()
        self._portfolio = PortfolioTracker(settings, starting_cash=self._config.starting_cash)
        self._risk = RiskManager(settings)

        self._l1_strategies = l1_strategies or []
        self._ml_strategy = ml_strategy
        self._nlp_by_step = nlp_signals_by_step or []
        self._decision_engine = decision_engine or DecisionEngine()
        self._active_layers = set(active_layers or ["l1", "l2", "l3"])

        self._trades: list[dict] = []
        self._equity_curve: list[float] = []
        self._pnl_by_market: dict[str, float] = {}
        self._pnl_by_layer: dict[str, float] = {"l1": 0.0, "l2": 0.0, "l3": 0.0}
        self._signals_by_layer: dict[str, int] = {"l1": 0, "l2": 0, "l3": 0}
        self._rng = np.random.default_rng(seed=42)

    def run(self, feature_snapshots: list[MarketFeatures]) -> BacktestResult:
        if not feature_snapshots:
            raise ValueError("No feature snapshots provided for backtest")

        logger.info(
            "multilayer_backtest_started",
            layers=sorted(self._active_layers),
            snapshots=len(feature_snapshots),
        )

        start_time = feature_snapshots[0].timestamp
        end_time = feature_snapshots[-1].timestamp

        for i, features in enumerate(feature_snapshots):
            portfolio_snap = self._portfolio.get_snapshot()

            l1_signals: list[NormalizedSignal] = []
            l2_signals: list[NormalizedSignal] = []
            l3_signals: list[NormalizedSignal] = []

            if "l1" in self._active_layers:
                for strat in self._l1_strategies:
                    sig = strat.generate_signal(features, portfolio_snap)
                    if sig is not None:
                        l1_signals.append(signal_to_normalized(sig, IntelligenceLayer.RULES))
                self._signals_by_layer["l1"] += len(l1_signals)

            if "l2" in self._active_layers and self._ml_strategy is not None:
                sig = self._ml_strategy.generate_signal(features, portfolio_snap)
                if sig is not None:
                    l2_signals.append(signal_to_normalized(sig, IntelligenceLayer.ML))
                self._signals_by_layer["l2"] += len(l2_signals)

            iid = features.instrument_id or features.token_id
            if "l3" in self._active_layers and i < len(self._nlp_by_step):
                for nlp_sig in self._nlp_by_step[i]:
                    l3_signals.append(nlp_signal_to_layered(nlp_sig, iid))
                self._signals_by_layer["l3"] += len(l3_signals)

            candidate, trace = self._decision_engine.evaluate(
                market_id=features.market_id,
                token_id=iid,
                features=features,
                portfolio=portfolio_snap,
                l1_signals=l1_signals,
                l2_signals=l2_signals,
                l3_signals=l3_signals,
                instrument_id=iid,
                exchange=features.exchange,
            )

            if not candidate.blocked and candidate.action != SignalAction.HOLD:
                contributing_layer = self._dominant_layer(candidate.layer_contributions)
                exec_signal = Signal(
                    strategy_name="decision_engine",
                    market_id=candidate.market_id,
                    token_id=candidate.instrument_id or candidate.token_id,
                    instrument_id=candidate.instrument_id or candidate.token_id,
                    exchange=candidate.exchange,
                    action=candidate.action,
                    confidence=candidate.final_confidence,
                    suggested_price=candidate.suggested_price,
                    suggested_size=candidate.suggested_size,
                    rationale=candidate.rationale,
                )
                self._simulate_execution(exec_signal, features, portfolio_snap, contributing_layer)

            snap = self._portfolio.get_snapshot()
            equity = snap.cash + sum(p.market_value for p in snap.positions)
            self._equity_curve.append(equity)

            if features.mid_price is not None:
                self._portfolio.mark_to_market(iid, features.mid_price)

        return self._compute_results(start_time, end_time)

    def _simulate_execution(
        self,
        signal: Signal,
        features: MarketFeatures,
        portfolio: PortfolioSnapshot,
        layer: str,
    ) -> None:
        side = _ACTION_TO_SIDE.get(signal.action)
        if side is None:
            return

        price = signal.suggested_price or (features.mid_price or 0.5)
        size = signal.suggested_size or self._settings.default_order_size
        iid = signal.instrument_id or signal.token_id

        risk_result = self._risk.check_order(
            instrument_id=iid,
            side=side,
            price=price,
            size=size,
            features=features,
            portfolio=portfolio,
        )
        if not risk_result.approved:
            return

        if self._rng.random() > self._config.fill_probability:
            return

        if side == Side.BUY:
            fill_price = price * (1 + self._config.slippage_bps)
        else:
            fill_price = price * (1 - self._config.slippage_bps)

        fee = size * fill_price * self._config.fee_rate

        order = Order(
            order_id=f"MLBT-{len(self._trades):06d}",
            market_id=signal.market_id,
            token_id=iid,
            instrument_id=iid,
            exchange=signal.exchange,
            side=side,
            price=fill_price,
            size=size,
            filled_size=size,
            status=OrderStatus.FILLED,
        )

        realized = self._portfolio.on_fill(order, fill_price, size)
        with self._portfolio._lock:
            self._portfolio._cash -= fee

        pnl = realized - fee if side == Side.SELL else -fee
        if side == Side.SELL:
            self._risk.record_fill(pnl)

        self._pnl_by_market.setdefault(signal.market_id, 0.0)
        self._pnl_by_market[signal.market_id] += pnl
        self._pnl_by_layer[layer] = self._pnl_by_layer.get(layer, 0.0) + pnl

        self._trades.append({
            "timestamp": features.timestamp.isoformat() if hasattr(features.timestamp, "isoformat") else str(features.timestamp),
            "market_id": signal.market_id,
            "instrument_id": iid,
            "exchange": signal.exchange,
            "side": side.value,
            "price": fill_price,
            "size": size,
            "fee": fee,
            "pnl": pnl,
            "layer": layer,
        })

    def _compute_results(self, start_time: datetime, end_time: datetime) -> BacktestResult:
        equity = np.array(self._equity_curve) if self._equity_curve else np.array([self._config.starting_cash])
        initial = self._config.starting_cash

        total_return = (equity[-1] - initial) / initial if initial > 0 else 0.0
        max_drawdown = BacktestEngine._compute_max_drawdown(equity)
        sharpe = BacktestEngine._compute_sharpe(equity)

        wins = sum(1 for t in self._trades if t["pnl"] > 0)
        total_trades = len(self._trades)
        win_rate = wins / total_trades if total_trades > 0 else 0.0

        exposure_sum = sum(self._equity_curve) - len(self._equity_curve) * initial
        exposure_util = abs(exposure_sum / (len(self._equity_curve) * initial)) if self._equity_curve else 0.0

        return BacktestResult(
            strategy_name="multi_layer",
            start_time=start_time,
            end_time=end_time,
            total_return=total_return,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            total_trades=total_trades,
            avg_trade_duration_seconds=0.0,
            sharpe_ratio=sharpe,
            exposure_utilization=exposure_util,
            pnl_by_market=dict(self._pnl_by_market),
            pnl_by_layer=dict(self._pnl_by_layer),
            signals_by_layer=dict(self._signals_by_layer),
            active_layers=sorted(self._active_layers),
            parameters={
                "fee_rate": self._config.fee_rate,
                "slippage_bps": self._config.slippage_bps * 10000,
                "fill_probability": self._config.fill_probability,
                "starting_cash": self._config.starting_cash,
                "active_layers": sorted(self._active_layers),
            },
        )

    @staticmethod
    def _dominant_layer(contributions: dict[str, float]) -> str:
        if not contributions:
            return "l1"
        return max(contributions, key=lambda k: abs(contributions.get(k, 0)))
