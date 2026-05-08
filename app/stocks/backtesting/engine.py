"""Stock-specific backtesting engine.

Replays a chronological list of bars per ticker through a strategy and
the deterministic risk gate. Outputs a :class:`StockBacktestResult` with
PnL, win rate, average win/loss, max drawdown, Sharpe-like metric, and a
trade log suitable for CSV export.

Limitations:
* Fills are assumed to occur at next-bar open (or current close if no
  next bar). Slippage and fees are deducted at fill time.
* Long-only by default. Sell signals close existing long positions; net
  short positions are not opened.
* No partial fills, no margin, no overnight financing.

These conservative simplifications keep the engine inspectable and
deterministic — the goal is research signal quality, not a tick-perfect
exchange simulator.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.data.models import PortfolioSnapshot
from app.stocks.features import StockFeatureEngine
from app.stocks.models import StockBar, StockSignal
from app.stocks.strategies.base import BaseStockStrategy


@dataclass
class StockBacktestConfig:
    starting_cash: float = 10000.0
    fee_per_trade: float = 0.0
    slippage_bps: float = 5.0  # 0.05% per side
    max_position_dollars: float = 1000.0
    require_stop_loss: bool = False  # bracket stops simulated when set


@dataclass
class StockTrade:
    symbol: str
    side: str
    entry_time: str
    entry_price: float
    exit_time: str | None
    exit_price: float | None
    quantity: int
    pnl: float
    strategy: str
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StockBacktestResult:
    strategy: str
    tickers: list[str]
    start: str
    end: str
    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    win_rate: float
    avg_win: float
    avg_loss: float
    max_drawdown: float
    sharpe: float
    starting_cash: float
    ending_cash: float
    trades: list[StockTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    def summary_dict(self) -> dict:
        d = asdict(self)
        d["trades"] = [t.to_dict() for t in self.trades]
        return d


# ── CSV loader ────────────────────────────────────────────────────────


_REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}


def load_csv_bars(path: str | Path, *, symbol: str | None = None) -> list[StockBar]:
    """Load a CSV of OHLCV bars into :class:`StockBar` objects.

    Expected columns: ``timestamp,open,high,low,close,volume`` (case
    insensitive). Optional ``symbol`` column overrides the argument.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"bar CSV not found: {p}")

    bars: list[StockBar] = []
    with p.open("r", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"empty CSV: {p}")
        cols = {c.lower(): c for c in reader.fieldnames}
        missing = _REQUIRED_COLUMNS - set(cols.keys())
        if missing:
            raise ValueError(
                f"CSV {p} missing columns: {sorted(missing)}"
            )
        for row in reader:
            ts = row[cols["timestamp"]]
            try:
                # Accept both ISO and epoch seconds.
                if ts.isdigit():
                    timestamp = datetime.fromtimestamp(int(ts))
                else:
                    timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            sym = (row.get(cols.get("symbol", "")) or symbol or "").strip()
            if not sym:
                raise ValueError("symbol column missing and not provided")
            try:
                bars.append(
                    StockBar(
                        symbol=sym.upper(),
                        timestamp=timestamp,
                        open=float(row[cols["open"]]),
                        high=float(row[cols["high"]]),
                        low=float(row[cols["low"]]),
                        close=float(row[cols["close"]]),
                        volume=int(float(row[cols["volume"]])),
                    )
                )
            except (TypeError, ValueError):
                continue

    bars.sort(key=lambda b: b.timestamp)
    return bars


# ── Engine ────────────────────────────────────────────────────────────


class StockBacktestEngine:
    """Run a single strategy across one or more tickers."""

    def __init__(
        self,
        strategy: BaseStockStrategy,
        config: StockBacktestConfig | None = None,
    ) -> None:
        self._strategy = strategy
        self._config = config or StockBacktestConfig()

    def run(self, bars_by_symbol: dict[str, list[StockBar]]) -> StockBacktestResult:
        cash = self._config.starting_cash
        positions: dict[str, dict] = {}  # symbol -> {qty, entry_price, entry_time, stop, rationale}
        trades: list[StockTrade] = []
        equity_curve: list[float] = []

        # Build a unified, time-ordered iterator across all symbols.
        events: list[tuple[datetime, str, StockBar]] = []
        for sym, bars in bars_by_symbol.items():
            for b in bars:
                events.append((b.timestamp, sym.upper(), b))
        events.sort(key=lambda e: e[0])
        if not events:
            raise ValueError("no bars provided to backtest engine")

        engines: dict[str, StockFeatureEngine] = {
            sym.upper(): StockFeatureEngine(sym.upper()) for sym in bars_by_symbol
        }

        start_time = events[0][0]
        end_time = events[-1][0]
        slippage = self._config.slippage_bps / 10000.0

        peak_equity = cash
        max_dd = 0.0

        for ts, sym, bar in events:
            engine = engines[sym]
            engine.add_bar(bar)
            engine.update_quote(bar.close, bar.close, bar.close)
            features = engine.compute()

            # Mark-to-market & stop checks.
            pos = positions.get(sym)
            if pos is not None and pos["qty"] > 0:
                stop = pos.get("stop")
                if stop is not None and bar.low <= stop:
                    exit_price = stop * (1 - slippage)
                    pnl = (exit_price - pos["entry_price"]) * pos["qty"] - self._config.fee_per_trade
                    cash += exit_price * pos["qty"] - self._config.fee_per_trade
                    trades.append(
                        StockTrade(
                            symbol=sym,
                            side="long",
                            entry_time=pos["entry_time"].isoformat(),
                            entry_price=pos["entry_price"],
                            exit_time=ts.isoformat(),
                            exit_price=exit_price,
                            quantity=pos["qty"],
                            pnl=pnl,
                            strategy=self._strategy.name,
                            rationale="ATR stop hit",
                        )
                    )
                    positions.pop(sym, None)
                    pos = None

            portfolio = PortfolioSnapshot(cash=cash, total_exposure=0.0)
            signal = self._strategy.generate_signal(features, portfolio)

            if signal is not None:
                cash_state = {"cash": cash}
                self._handle_signal(
                    signal=signal,
                    sym=sym,
                    ts=ts,
                    bar=bar,
                    positions=positions,
                    cash_state=cash_state,
                    trades=trades,
                    slippage=slippage,
                )
                cash = cash_state["cash"]

            # Update equity.
            mtm = sum(
                p["qty"] * bar.close
                for s, p in positions.items()
                if s == sym
            )
            mtm_other = 0.0
            for s, p in positions.items():
                if s != sym:
                    last_bar = bars_by_symbol.get(s, [])
                    if last_bar:
                        mtm_other += p["qty"] * last_bar[-1].close
            equity = cash + mtm + mtm_other
            equity_curve.append(equity)
            peak_equity = max(peak_equity, equity)
            max_dd = max(max_dd, peak_equity - equity)

        # Force-close any open long position at the last bar's close.
        last_close_by_sym = {
            sym: bars[-1].close for sym, bars in bars_by_symbol.items() if bars
        }
        for sym, p in list(positions.items()):
            close = last_close_by_sym.get(sym, p["entry_price"])
            exit_price = close * (1 - slippage)
            pnl = (exit_price - p["entry_price"]) * p["qty"] - self._config.fee_per_trade
            cash += exit_price * p["qty"] - self._config.fee_per_trade
            trades.append(
                StockTrade(
                    symbol=sym,
                    side="long",
                    entry_time=p["entry_time"].isoformat(),
                    entry_price=p["entry_price"],
                    exit_time=end_time.isoformat(),
                    exit_price=exit_price,
                    quantity=p["qty"],
                    pnl=pnl,
                    strategy=self._strategy.name,
                    rationale="forced close at backtest end",
                )
            )
        positions.clear()

        return self._build_result(
            tickers=sorted(bars_by_symbol.keys()),
            start=start_time,
            end=end_time,
            trades=trades,
            equity_curve=equity_curve,
            max_dd=max_dd,
            ending_cash=cash,
        )

    # ── Internal helpers ──────────────────────────────────────────────

    def _handle_signal(
        self,
        *,
        signal: StockSignal,
        sym: str,
        ts: datetime,
        bar: StockBar,
        positions: dict[str, dict],
        cash_state: dict,
        trades: list[StockTrade],
        slippage: float,
    ) -> None:
        action = signal.action.value.upper()
        cash = cash_state["cash"]

        if action == "BUY" and sym not in positions:
            entry_price = bar.close * (1 + slippage)
            max_dollars = self._config.max_position_dollars
            qty = max(1, int(min(max_dollars, cash) / entry_price)) if entry_price > 0 else 0
            if qty <= 0 or qty * entry_price > cash:
                return
            cash -= entry_price * qty + self._config.fee_per_trade
            positions[sym] = {
                "qty": qty,
                "entry_price": entry_price,
                "entry_time": ts,
                "stop": signal.stop_price,
                "rationale": signal.rationale,
            }
            cash_state["cash"] = cash
            trades.append(
                StockTrade(
                    symbol=sym,
                    side="long_entry",
                    entry_time=ts.isoformat(),
                    entry_price=entry_price,
                    exit_time=None,
                    exit_price=None,
                    quantity=qty,
                    pnl=0.0,
                    strategy=signal.strategy_name,
                    rationale=signal.rationale,
                )
            )

        elif action == "SELL" and sym in positions:
            pos = positions.pop(sym)
            exit_price = bar.close * (1 - slippage)
            pnl = (exit_price - pos["entry_price"]) * pos["qty"] - self._config.fee_per_trade
            cash += exit_price * pos["qty"] - self._config.fee_per_trade
            cash_state["cash"] = cash
            trades.append(
                StockTrade(
                    symbol=sym,
                    side="long",
                    entry_time=pos["entry_time"].isoformat(),
                    entry_price=pos["entry_price"],
                    exit_time=ts.isoformat(),
                    exit_price=exit_price,
                    quantity=pos["qty"],
                    pnl=pnl,
                    strategy=signal.strategy_name,
                    rationale=signal.rationale,
                )
            )

    def _build_result(
        self,
        *,
        tickers: list[str],
        start: datetime,
        end: datetime,
        trades: list[StockTrade],
        equity_curve: list[float],
        max_dd: float,
        ending_cash: float,
    ) -> StockBacktestResult:
        completed = [t for t in trades if t.exit_time is not None]
        wins = [t for t in completed if t.pnl > 0]
        losses = [t for t in completed if t.pnl < 0]
        total_pnl = sum(t.pnl for t in completed)

        sharpe = 0.0
        if len(equity_curve) > 2:
            diffs = [equity_curve[i] - equity_curve[i - 1] for i in range(1, len(equity_curve))]
            if diffs:
                mean = sum(diffs) / len(diffs)
                var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
                std = math.sqrt(var)
                if std > 0:
                    sharpe = (mean / std) * math.sqrt(252)

        return StockBacktestResult(
            strategy=self._strategy.name,
            tickers=tickers,
            start=start.isoformat(),
            end=end.isoformat(),
            total_trades=len(completed),
            wins=len(wins),
            losses=len(losses),
            total_pnl=total_pnl,
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
