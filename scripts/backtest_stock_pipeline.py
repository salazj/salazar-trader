#!/usr/bin/env python3
"""Backtest the FULL decision pipeline (L1+L2+L3+risk) over historical bars.

Unlike ``backtest_stock_strategy.py`` (single strategy), this replays bars
through the production decision path: all strategies compete, the ML model
(if present) and a neutral LLM verdict feed the fusion engine, the risk gate
runs, and entries get bracket-style stop/target exits.

Examples:

    python scripts/backtest_stock_pipeline.py --tickers SPY,QQQ,NVDA \\
        --data-dir data/bars --min-score 0.40

    python scripts/backtest_stock_pipeline.py --tickers NVDA \\
        --data-dir data/bars --ml-model model_artifacts/stock_xgb_v1.pkl

CSV files named ``<TICKER>.csv`` with columns
``timestamp,open,high,low,close,volume`` are expected in ``--data-dir``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import Settings  # noqa: E402
from app.stocks.backtesting import (  # noqa: E402
    StockBacktestConfig,
    StockPipelineBacktestEngine,
    load_csv_bars,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tickers", required=True, help="comma separated tickers")
    p.add_argument("--data-dir", default="data/bars")
    p.add_argument("--starting-cash", type=float, default=25000.0)
    p.add_argument("--max-position-dollars", type=float, default=2000.0)
    p.add_argument("--min-score", type=float, default=None, help="override stock_min_final_score")
    p.add_argument("--ml-model", default=None, help="path to a trained stock ML pickle")
    p.add_argument("--json", action="store_true", help="emit JSON summary")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    data_dir = Path(args.data_dir)

    bars_by_symbol = {}
    for ticker in tickers:
        path = data_dir / f"{ticker}.csv"
        if not path.exists():
            print(f"WARNING: {path} not found, skipping", file=sys.stderr)
            continue
        bars_by_symbol[ticker] = load_csv_bars(path, symbol=ticker)
    if not bars_by_symbol:
        print("ERROR: no bar data loaded", file=sys.stderr)
        return 2

    overrides = {"approved_stock_tickers": ",".join(tickers)}
    if args.min_score is not None:
        overrides["stock_min_final_score"] = args.min_score
    settings = Settings(**overrides)

    ml_predictor = None
    if args.ml_model:
        from app.stocks.ml.predictor import StockMLPredictor

        ml_predictor = StockMLPredictor.load(args.ml_model)

    engine = StockPipelineBacktestEngine(
        settings,
        ml_predictor=ml_predictor,
        config=StockBacktestConfig(
            starting_cash=args.starting_cash,
            max_position_dollars=args.max_position_dollars,
        ),
    )
    result = engine.run(bars_by_symbol)

    if args.json:
        print(json.dumps(result.summary_dict(), indent=2, default=str))
    else:
        print(f"\n{'='*52}\nFULL-PIPELINE BACKTEST\n{'='*52}")
        print(f"Tickers       : {', '.join(result.tickers)}")
        print(f"Period        : {result.start}  →  {result.end}")
        print(f"Trades        : {result.total_trades} "
              f"(W {result.wins} / L {result.losses})")
        print(f"Win rate      : {result.win_rate:.1%}")
        print(f"Total PnL     : ${result.total_pnl:,.2f}")
        print(f"Avg win/loss  : ${result.avg_win:,.2f} / ${result.avg_loss:,.2f}")
        print(f"Max drawdown  : ${result.max_drawdown:,.2f}")
        print(f"Sharpe        : {result.sharpe:.2f}")
        print(f"Cash          : ${result.starting_cash:,.0f} → ${result.ending_cash:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
