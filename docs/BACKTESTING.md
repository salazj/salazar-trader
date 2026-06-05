# Stock Backtesting & Walk-Forward Validation

The Jetson stock pipeline ships its own purpose-built backtester so you
can iterate on strategies offline before flipping `DRY_RUN=false`.

* Engine: `app.stocks.backtesting.StockBacktestEngine`
* Walk-forward: `app.stocks.backtesting.run_walk_forward`
* CLI: `scripts/backtest_stock_strategy.py`

---

## CLI

```bash
python scripts/backtest_stock_strategy.py \
  --strategy stock_momentum \
  --tickers SPY,QQQ,NVDA \
  --start 2024-01-01 \
  --end   2024-12-31 \
  --data-dir data/bars
```

CSV layout under `--data-dir`:

```
data/bars/SPY.csv
data/bars/QQQ.csv
data/bars/NVDA.csv
```

Each file must have columns `timestamp, open, high, low, close, volume`
(case-insensitive) and ISO8601 or epoch-second timestamps. Use
`--synthetic` to bypass the CSV requirement and generate a synthetic
mean-reverting random-walk dataset for smoke tests.

Available strategies (use the `--strategy` value):

* `stock_momentum`
* `stock_mean_reversion`
* `stock_breakout`
* `stock_pullback`
* `stock_news_gated`

Output: a JSON report saved under `reports/` (or `--output`) plus a
short summary printed to stdout:

```json
{
  "strategy": "stock_momentum",
  "total_trades": 42,
  "win_rate": 0.5238,
  "total_pnl": 318.41,
  "avg_win": 41.2,
  "avg_loss": -22.7,
  "max_drawdown": 124.0,
  "sharpe": 1.21,
  "starting_cash": 10000.0,
  "ending_cash": 10318.41
}
```

---

## Walk-forward validation

Pass `--walk-forward` to run chronological splits — never random
shuffles. Each split trains on the past `--train-size` bars and tests
on the next `--test-size` bars:

```bash
python scripts/backtest_stock_strategy.py \
  --strategy stock_pullback \
  --tickers SPY,QQQ \
  --data-dir data/bars \
  --walk-forward --train-size 1500 --test-size 250
```

The report includes per-split metrics and aggregated out-of-sample
PnL / win rate / Sharpe. **Never optimise hyper-parameters on the same
data used for the final validation split** — bias your hyperparameter
sweeps to the in-sample window.

---

## REST endpoints

```bash
# List recent reports
curl http://localhost:8000/api/backtests

# Run a backtest (synthetic data smoke test)
curl -X POST http://localhost:8000/api/backtests/run \
  -H 'Content-Type: application/json' \
  -d '{"strategy":"stock_momentum","tickers":["SPY"],"use_synthetic":true}'
```

Reports are written to `reports/backtest_<strategy>_<kind>_<utc>.json`.

---

## Limitations & caveats

* Long-only by default. Sell signals close existing long positions; a
  net short is not opened.
* Fills are at next-bar close (or the same bar's close if no next bar).
* Slippage is a fixed bps offset per side. Real slippage scales with
  size vs liquidity — increase `--slippage-bps` for less-liquid tickers.
* No partial fills, no margin, no overnight financing.
* The deterministic risk gate (`StockRiskManager`) is **not** invoked in
  the pure backtester — that's intentional: backtests measure *strategy*
  signal quality. The decision-engine integration test (`tests/`) exercises
  the full L1/L2/L3 + risk path.
