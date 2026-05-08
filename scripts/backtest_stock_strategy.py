#!/usr/bin/env python3
"""Backtest a stock strategy over historical OHLCV data.

Examples:

    python scripts/backtest_stock_strategy.py \\
        --strategy stock_momentum \\
        --tickers SPY,QQQ,NVDA \\
        --start 2024-01-01 \\
        --end 2024-12-31 \\
        --data-dir data/bars

    # Walk-forward validation:
    python scripts/backtest_stock_strategy.py \\
        --strategy stock_pullback \\
        --tickers SPY,QQQ \\
        --data-dir data/bars \\
        --walk-forward --train-size 1500 --test-size 250

The script expects CSV files named ``<TICKER>.csv`` inside ``--data-dir``,
each with columns ``timestamp,open,high,low,close,volume``.

If ``--synthetic`` is passed instead, a small synthetic dataset is
generated to demonstrate the engine without a network connection.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the ``app`` package importable when this script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.stocks.backtesting import (  # noqa: E402
    StockBacktestConfig,
    StockBacktestEngine,
    load_csv_bars,
    run_walk_forward,
)
from app.stocks.models import StockBar  # noqa: E402
from app.stocks.strategies import STRATEGY_REGISTRY  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--strategy",
        default="stock_momentum",
        choices=sorted(STRATEGY_REGISTRY.keys()),
        help="strategy name",
    )
    p.add_argument(
        "--tickers",
        default="SPY,QQQ,NVDA",
        help="comma separated tickers",
    )
    p.add_argument("--start", default=None, help="ISO start date filter")
    p.add_argument("--end", default=None, help="ISO end date filter")
    p.add_argument(
        "--data-dir",
        default="data/bars",
        help="directory containing <ticker>.csv files",
    )
    p.add_argument("--starting-cash", type=float, default=10000.0)
    p.add_argument("--max-position-dollars", type=float, default=1000.0)
    p.add_argument("--fee-per-trade", type=float, default=0.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--output", default="reports/backtest_stock.json")

    p.add_argument(
        "--walk-forward",
        action="store_true",
        help="run walk-forward validation instead of a single backtest",
    )
    p.add_argument("--train-size", type=int, default=1000)
    p.add_argument("--test-size", type=int, default=250)
    p.add_argument("--step", type=int, default=None)

    p.add_argument(
        "--synthetic",
        action="store_true",
        help="generate a synthetic dataset (smoke test)",
    )
    return p.parse_args()


def _make_synthetic(tickers: list[str]) -> dict[str, list[StockBar]]:
    """Generate a small synthetic dataset (mean-reverting random walk)."""
    bars_by_symbol: dict[str, list[StockBar]] = {}
    base_ts = datetime(2024, 1, 2, 13, 30, tzinfo=timezone.utc)
    for sym in tickers:
        price = 100.0
        bars: list[StockBar] = []
        for i in range(2000):
            drift = math.sin(i / 60.0) * 0.5
            shock = math.cos(i * 0.13) * 0.6
            close = max(1.0, price + drift + shock)
            high = close + 0.4
            low = close - 0.4
            open_ = price
            vol = 100_000 + int(math.sin(i / 7.0) * 30_000) + (10_000 if i % 17 == 0 else 0)
            bars.append(
                StockBar(
                    symbol=sym,
                    timestamp=base_ts + timedelta(minutes=i),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=vol,
                )
            )
            price = close
        bars_by_symbol[sym] = bars
    return bars_by_symbol


def _load(args: argparse.Namespace, tickers: list[str]) -> dict[str, list[StockBar]]:
    if args.synthetic:
        return _make_synthetic(tickers)
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        raise SystemExit(
            f"data dir {data_dir} not found. Pass --synthetic to use a "
            f"synthetic dataset, or supply CSVs."
        )
    out: dict[str, list[StockBar]] = {}
    start = (
        datetime.fromisoformat(args.start.replace("Z", "+00:00"))
        if args.start
        else None
    )
    end = (
        datetime.fromisoformat(args.end.replace("Z", "+00:00"))
        if args.end
        else None
    )
    for sym in tickers:
        path = data_dir / f"{sym.upper()}.csv"
        if not path.exists():
            raise SystemExit(f"missing CSV for {sym}: {path}")
        bars = load_csv_bars(path, symbol=sym)
        if start is not None:
            bars = [b for b in bars if b.timestamp >= start]
        if end is not None:
            bars = [b for b in bars if b.timestamp <= end]
        if not bars:
            raise SystemExit(f"no bars left for {sym} after filtering")
        out[sym.upper()] = bars
    return out


def main() -> int:
    args = parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    bars_by_symbol = _load(args, tickers)

    strategy_cls = STRATEGY_REGISTRY[args.strategy]
    config = StockBacktestConfig(
        starting_cash=args.starting_cash,
        max_position_dollars=args.max_position_dollars,
        fee_per_trade=args.fee_per_trade,
        slippage_bps=args.slippage_bps,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.walk_forward:
        report = run_walk_forward(
            strategy_factory=strategy_cls,
            bars_by_symbol=bars_by_symbol,
            train_size=args.train_size,
            test_size=args.test_size,
            step=args.step,
            config=config,
        )
        out_path.write_text(json.dumps(report.to_dict(), indent=2, default=str))
        print(json.dumps(report.to_dict(), indent=2, default=str))
        return 0

    engine = StockBacktestEngine(strategy_cls(), config=config)
    result = engine.run(bars_by_symbol)
    out_path.write_text(json.dumps(result.summary_dict(), indent=2, default=str))

    summary = {
        "strategy": result.strategy,
        "tickers": result.tickers,
        "total_trades": result.total_trades,
        "win_rate": round(result.win_rate, 4),
        "total_pnl": round(result.total_pnl, 2),
        "avg_win": round(result.avg_win, 2),
        "avg_loss": round(result.avg_loss, 2),
        "max_drawdown": round(result.max_drawdown, 2),
        "sharpe": round(result.sharpe, 3),
        "starting_cash": result.starting_cash,
        "ending_cash": round(result.ending_cash, 2),
    }
    print(json.dumps(summary, indent=2))
    print(f"\nFull report saved to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
