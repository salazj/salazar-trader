"""Full-pipeline stock backtester.

Unlike :class:`StockBacktestEngine` (which replays a *single* strategy), this
engine replays bars through the **production decision path**:

    all L1 strategies → pick best → L2 ML (optional) → L3 (neutral by default)
        → StockDecisionEngine.evaluate (fusion + threshold + risk gate)
        → bracket-style exits (ATR stop / take-profit / strategy SELL / EOD)

It answers "would the bot, as configured, have made money?" rather than just
"is this one strategy's signal any good?". L3 (LLM) is held neutral by default
since it isn't available offline; pass a callable to inject verdicts.

Conservative fill model: entries fill at next bar's open (or current close),
stops/targets fill at the level touched, all with symmetric slippage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from app.config.settings import Settings
from app.data.models import PortfolioSnapshot
from app.llm.schema import LLMVerdict, safe_default_verdict
from app.stocks.backtesting.engine import (
    StockBacktestConfig,
    StockBacktestResult,
    StockTrade,
)
from app.stocks.decision.engine import StockDecisionAction, StockDecisionEngine
from app.stocks.features import StockFeatureEngine
from app.stocks.models import StockBar, StockFeatures
from app.stocks.risk import StockRiskManager
from app.stocks.strategies import ALL_STOCK_STRATEGIES


class StockPipelineBacktestEngine:
    """Replay bars through the full L1+L2+L3 decision + risk pipeline."""

    def __init__(
        self,
        settings: Settings,
        *,
        strategies: list | None = None,
        ml_predictor=None,
        llm_verdict_fn: Callable[[str, StockFeatures], LLMVerdict] | None = None,
        config: StockBacktestConfig | None = None,
    ) -> None:
        self._settings = settings
        self._strategies = strategies or [s() for s in ALL_STOCK_STRATEGIES]
        self._ml = ml_predictor
        self._llm_fn = llm_verdict_fn
        self._config = config or StockBacktestConfig()
        # Replaying historical bars: disable the wall-clock data-staleness gate
        # (bar timestamps are intentionally in the past) while keeping every
        # other risk control intact.
        try:
            bt_settings = settings.model_copy(
                update={"stock_max_bar_age_seconds": 10**12}
            )
        except Exception:
            bt_settings = settings
        self._risk = StockRiskManager(bt_settings)
        self._engine = StockDecisionEngine(bt_settings, risk_manager=self._risk)

    def run(self, bars_by_symbol: dict[str, list[StockBar]]) -> StockBacktestResult:
        cash = self._config.starting_cash
        positions: dict[str, dict] = {}
        trades: list[StockTrade] = []
        equity_curve: list[float] = []

        events: list[tuple[datetime, str, StockBar]] = []
        for sym, bars in bars_by_symbol.items():
            for b in bars:
                events.append((b.timestamp, sym.upper(), b))
        events.sort(key=lambda e: e[0])
        if not events:
            raise ValueError("no bars provided to backtest engine")

        engines = {s.upper(): StockFeatureEngine(s.upper()) for s in bars_by_symbol}
        last_close: dict[str, float] = {}
        slippage = self._config.slippage_bps / 10000.0
        start_time, end_time = events[0][0], events[-1][0]
        peak_equity, max_dd = cash, 0.0

        for ts, sym, bar in events:
            engine = engines[sym]
            engine.add_bar(bar)
            engine.update_quote(bar.close, bar.close, bar.close)
            features = engine.compute()
            last_close[sym] = bar.close

            # ── Exits first: bracket stop / target on the current bar ──────
            pos = positions.get(sym)
            if pos is not None:
                exit_price = exit_reason = None
                if pos.get("stop") is not None and bar.low <= pos["stop"]:
                    exit_price = pos["stop"] * (1 - slippage)
                    exit_reason = "stop"
                elif pos.get("target") is not None and bar.high >= pos["target"]:
                    exit_price = pos["target"] * (1 - slippage)
                    exit_reason = "target"
                if exit_price is not None:
                    cash += exit_price * pos["qty"] - self._config.fee_per_trade
                    trades.append(self._close_trade(sym, pos, ts, exit_price, exit_reason))
                    positions.pop(sym, None)
                    pos = None

            # ── Decision pipeline ──────────────────────────────────────────
            total_exposure = sum(
                p["qty"] * last_close.get(s, p["entry_price"])
                for s, p in positions.items()
            )
            portfolio = PortfolioSnapshot(cash=cash, total_exposure=total_exposure)

            best = self._select_l1(features, portfolio)
            if best is not None:
                ml_pred = self._ml.predict(features) if self._ml is not None else None
                verdict = (
                    self._llm_fn(sym, features)
                    if self._llm_fn is not None
                    else safe_default_verdict(sym)
                )
                trace = self._engine.evaluate(
                    sym, features, portfolio,
                    l1_signal=best, ml_prediction=ml_pred,
                    llm_verdict=verdict, regime=None, broker=None,
                )
                if trace.action == StockDecisionAction.BUY and sym not in positions:
                    cash = self._enter(sym, best, bar, ts, cash, positions, slippage)
                elif trace.action == StockDecisionAction.SELL and sym in positions:
                    p = positions.pop(sym)
                    exit_price = bar.close * (1 - slippage)
                    cash += exit_price * p["qty"] - self._config.fee_per_trade
                    trades.append(self._close_trade(sym, p, ts, exit_price, "signal_exit"))

            equity = cash + sum(
                p["qty"] * last_close.get(s, p["entry_price"])
                for s, p in positions.items()
            )
            equity_curve.append(equity)
            peak_equity = max(peak_equity, equity)
            max_dd = max(max_dd, peak_equity - equity)

        # Force-close at the end.
        for sym, p in list(positions.items()):
            close = last_close.get(sym, p["entry_price"])
            exit_price = close * (1 - slippage)
            cash += exit_price * p["qty"] - self._config.fee_per_trade
            trades.append(self._close_trade(sym, p, end_time, exit_price, "forced_close"))
        positions.clear()

        return self._build_result(
            sorted(bars_by_symbol.keys()), start_time, end_time,
            trades, equity_curve, max_dd, cash,
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _select_l1(self, features: StockFeatures, portfolio: PortfolioSnapshot):
        best, best_conf = None, -1.0
        for strat in self._strategies:
            try:
                sig = strat.generate_signal(features, portfolio)
            except Exception:
                continue
            if sig is None or sig.action.value == "HOLD":
                continue
            if sig.confidence > best_conf:
                best_conf, best = sig.confidence, sig
        return best

    def _enter(self, sym, signal, bar, ts, cash, positions, slippage) -> float:
        entry_price = bar.close * (1 + slippage)
        if entry_price <= 0:
            return cash
        qty = self._size(entry_price, cash, signal.stop_price)
        if qty <= 0 or qty * entry_price > cash:
            return cash
        cash -= entry_price * qty + self._config.fee_per_trade
        positions[sym] = {
            "qty": qty,
            "entry_price": entry_price,
            "entry_time": ts,
            "stop": signal.stop_price,
            "target": signal.target_price,
            "strategy": signal.strategy_name,
            "rationale": signal.rationale,
        }
        return cash

    def _size(self, price: float, cash: float, stop: float | None) -> int:
        max_dollars = min(self._config.max_position_dollars, cash * 0.25)
        notional_cap = int(max_dollars / price) if price > 0 else 0
        risk_dollars = float(getattr(self._settings, "stock_risk_per_trade_dollars", 0) or 0)
        if risk_dollars > 0 and stop is not None and 0 < stop < price:
            risk_shares = int(risk_dollars / (price - stop))
            return max(0, min(risk_shares, notional_cap))
        return max(1, notional_cap) if notional_cap >= 1 else 0

    def _close_trade(self, sym, pos, ts, exit_price, reason) -> StockTrade:
        pnl = (exit_price - pos["entry_price"]) * pos["qty"] - self._config.fee_per_trade
        return StockTrade(
            symbol=sym,
            side="long",
            entry_time=pos["entry_time"].isoformat(),
            entry_price=pos["entry_price"],
            exit_time=ts.isoformat(),
            exit_price=exit_price,
            quantity=pos["qty"],
            pnl=pnl,
            strategy=pos.get("strategy", "pipeline"),
            rationale=reason,
        )

    def _build_result(
        self, tickers, start, end, trades, equity_curve, max_dd, ending_cash
    ) -> StockBacktestResult:
        import math

        completed = [t for t in trades if t.exit_time is not None]
        wins = [t for t in completed if t.pnl > 0]
        losses = [t for t in completed if t.pnl < 0]
        sharpe = 0.0
        if len(equity_curve) > 2:
            diffs = [equity_curve[i] - equity_curve[i - 1] for i in range(1, len(equity_curve))]
            mean = sum(diffs) / len(diffs)
            var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
            std = math.sqrt(var)
            if std > 0:
                sharpe = (mean / std) * math.sqrt(252)
        return StockBacktestResult(
            strategy="full_pipeline",
            tickers=tickers,
            start=start.isoformat(),
            end=end.isoformat(),
            total_trades=len(completed),
            wins=len(wins),
            losses=len(losses),
            total_pnl=sum(t.pnl for t in completed),
            win_rate=(len(wins) / len(completed)) if completed else 0.0,
            avg_win=(sum(t.pnl for t in wins) / len(wins)) if wins else 0.0,
            avg_loss=(sum(t.pnl for t in losses) / len(losses)) if losses else 0.0,
            max_drawdown=max_dd,
            sharpe=sharpe,
            starting_cash=self._config.starting_cash,
            ending_cash=ending_cash,
            trades=trades,
            equity_curve=equity_curve,
        )
