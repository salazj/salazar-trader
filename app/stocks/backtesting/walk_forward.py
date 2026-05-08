"""Walk-forward validation utilities.

The walk-forward harness *never* shuffles time-series data: each split
trains the strategy on the past window and validates on the immediate
future. We report per-split metrics plus an aggregated out-of-sample
summary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable

from app.stocks.backtesting.engine import (
    StockBacktestConfig,
    StockBacktestEngine,
    StockBacktestResult,
)
from app.stocks.models import StockBar
from app.stocks.strategies.base import BaseStockStrategy


@dataclass
class WalkForwardSplit:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def walk_forward_splits(
    n: int,
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    min_train: int = 1,
) -> list[WalkForwardSplit]:
    """Build chronological train/test splits.

    Parameters
    ----------
    n
        Total number of bars.
    train_size
        Train window length in bars.
    test_size
        Out-of-sample window length in bars.
    step
        Step between consecutive splits. Defaults to ``test_size``.
    """
    if train_size < min_train:
        raise ValueError("train_size too small")
    if test_size <= 0:
        raise ValueError("test_size must be positive")
    step = step or test_size
    splits: list[WalkForwardSplit] = []
    cursor = 0
    while cursor + train_size + test_size <= n:
        splits.append(
            WalkForwardSplit(
                train_start=cursor,
                train_end=cursor + train_size,
                test_start=cursor + train_size,
                test_end=cursor + train_size + test_size,
            )
        )
        cursor += step
    return splits


@dataclass
class WalkForwardReport:
    strategy: str
    splits: int
    train_size: int
    test_size: int
    out_of_sample_pnl: float = 0.0
    out_of_sample_win_rate: float = 0.0
    out_of_sample_sharpe: float = 0.0
    per_split: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_walk_forward(
    strategy_factory: Callable[[], BaseStockStrategy],
    bars_by_symbol: dict[str, list[StockBar]],
    *,
    train_size: int,
    test_size: int,
    config: StockBacktestConfig | None = None,
    step: int | None = None,
) -> WalkForwardReport:
    """Run walk-forward validation across all symbols simultaneously.

    For each split we backtest only the *test* window using a freshly
    constructed strategy instance — this prevents accidental state leak
    from train into test.
    """
    if not bars_by_symbol:
        raise ValueError("bars_by_symbol is empty")

    base_symbol = next(iter(bars_by_symbol))
    n = len(bars_by_symbol[base_symbol])
    if any(len(bars) != n for bars in bars_by_symbol.values()):
        raise ValueError("all symbols must have the same number of bars")

    splits = walk_forward_splits(
        n, train_size=train_size, test_size=test_size, step=step
    )

    pnl_total = 0.0
    win_total = 0
    trade_total = 0
    sharpes: list[float] = []
    per_split: list[dict] = []

    for split in splits:
        test_bars = {
            sym: bars[split.test_start : split.test_end]
            for sym, bars in bars_by_symbol.items()
        }
        engine = StockBacktestEngine(
            strategy=strategy_factory(),
            config=config or StockBacktestConfig(),
        )
        result = engine.run(test_bars)
        pnl_total += result.total_pnl
        win_total += result.wins
        trade_total += result.total_trades
        sharpes.append(result.sharpe)
        per_split.append(
            {
                "split": asdict(split),
                "trades": result.total_trades,
                "pnl": result.total_pnl,
                "win_rate": result.win_rate,
                "sharpe": result.sharpe,
                "max_drawdown": result.max_drawdown,
            }
        )

    return WalkForwardReport(
        strategy=strategy_factory().name,
        splits=len(splits),
        train_size=train_size,
        test_size=test_size,
        out_of_sample_pnl=pnl_total,
        out_of_sample_win_rate=(win_total / trade_total) if trade_total else 0.0,
        out_of_sample_sharpe=sum(sharpes) / len(sharpes) if sharpes else 0.0,
        per_split=per_split,
    )
