# Risk Controls

Risk control is the **single source of truth** for whether an order
leaves this process. Neither the LLM, the ML predictor, nor the
strategy ensemble can bypass it — all three feed into the decision
engine, which then asks `StockRiskManager.check_order()`. If the gate
denies the order, the order is dropped and a `StockDecisionTrace` is
recorded with `action="blocked"` and the reason.

---

## Beginner-safe Jetson defaults

| Setting                                  | Default | Purpose                                  |
|------------------------------------------|--------:|------------------------------------------|
| `STOCK_MAX_POSITION_DOLLARS`             | `50`    | Max notional per order.                  |
| `STOCK_MAX_PORTFOLIO_DOLLARS`            | `250`   | Max total stock exposure.                |
| `STOCK_MAX_DAILY_LOSS_DOLLARS`           | `25`    | Daily-loss circuit breaker.              |
| `STOCK_MAX_OPEN_POSITIONS`               | `3`     | Max concurrent positions.                |
| `STOCK_MAX_ORDERS_PER_MINUTE`            | `3`     | Order frequency cap.                     |
| `STOCK_MAX_TRADES_PER_DAY`               | `5`     | New entries per day.                     |
| `STOCK_REQUIRE_STOP_LOSS`                | `true`  | Buys must include a stop price.          |
| `STOCK_MAX_CONSECUTIVE_LOSSES_PER_SYMBOL`| `2`     | Revenge-trade guard.                     |
| `STOCK_MAX_BAR_AGE_SECONDS`              | `120`   | Reject stale market data.                |
| `ALLOW_EXTENDED_HOURS`                   | `false` | Reject trades outside RTH.               |
| `APPROVED_STOCK_TICKERS`                 | SPY,QQQ,AAPL,MSFT,NVDA,TSLA,AMD,META,AMZN,GOOGL | Universe allow-list. |

These defaults are intentional and tiny — they let a beginner run for
weeks on paper without losing more than $25 in any session.

---

## Pre-trade gate order

`StockRiskManager.check_order` runs each gate in order and returns the
first denial. The `checks` list on the result records which gates were
evaluated, even on approval.

1. Emergency stop file present (`./EMERGENCY_STOP`).
2. Circuit breaker tripped.
3. Daily loss limit exceeded.
4. Ticker not in `APPROVED_STOCK_TICKERS`.
5. Bar timestamp older than `STOCK_MAX_BAR_AGE_SECONDS`.
6. Buy order missing a stop price (when `STOCK_REQUIRE_STOP_LOSS=true`).
7. Order notional > `STOCK_MAX_POSITION_DOLLARS`.
8. Portfolio + order > `STOCK_MAX_PORTFOLIO_DOLLARS`.
9. Open positions >= `STOCK_MAX_OPEN_POSITIONS` on a buy.
10. Trades-today >= `STOCK_MAX_TRADES_PER_DAY` on a buy.
11. Per-minute order rate > `STOCK_MAX_ORDERS_PER_MINUTE`.
12. Market closed (when `ALLOW_EXTENDED_HOURS=false`).
13. Insufficient cash on a buy.
14. Revenge-trade guard: same symbol with `>= STOCK_MAX_CONSECUTIVE_LOSSES_PER_SYMBOL` recent losses.

---

## Three-gate live trading lock

Live orders require all three of:

```
DRY_RUN=false
ENABLE_LIVE_TRADING=true
LIVE_TRADING_ACKNOWLEDGED=true
```

Plus valid `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`. Anything less and
the bot stays in dry-run / paper mode.

---

## Emergency stop

Two paths:

* `./EMERGENCY_STOP` file: the risk manager refuses every order while
  this file exists. Useful for `kill-switch` cron jobs.
* `POST /api/risk/emergency-stop` with body `{"confirm": true}`. The
  confirm flag is required; the dashboard shows a confirmation modal.

`POST /api/risk/reset-circuit-breaker` clears a tripped breaker — the
bot does **not** auto-reset on its own.
