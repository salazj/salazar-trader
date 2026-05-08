# Stock Trading (Alpaca, Jetson Orin Nano)

Salazar-Trader's equities path is the primary use-case on Jetson Orin
Nano. It targets stocks and ETFs through the Alpaca paper/live API
with deterministic risk controls and a three-layer decision engine.

---

## Quick start

1. Sign up at <https://app.alpaca.markets/> and get a paper API key.
2. Copy `.env.example` to `.env` and set:

   ```
   ASSET_CLASS=equities
   BROKER=alpaca
   ALPACA_API_KEY=...
   ALPACA_SECRET_KEY=...
   ALPACA_PAPER=true
   DRY_RUN=true
   ENABLE_LIVE_TRADING=false
   LIVE_TRADING_ACKNOWLEDGED=false
   ```
3. Run:

   ```bash
   bash scripts/setup_jetson.sh    # first time only
   source .venv/bin/activate
   python -m app.api
   ```

Open the dashboard at `http://<jetson-ip>:3000`.

---

## Modes

| Mode                         | Required env                                                          | Behaviour                                |
|------------------------------|-----------------------------------------------------------------------|------------------------------------------|
| **Dry run (default)**        | `DRY_RUN=true`                                                        | Simulates fills locally — no broker API. |
| **Alpaca paper trading**     | `DRY_RUN=false`, `ALPACA_PAPER=true`                                  | Sends orders to Alpaca paper endpoint.   |
| **Live trading**             | All three gates true + creds present                                  | Real orders. Requires explicit GUI ack.  |

Live trading is locked behind:

```
DRY_RUN=false
ENABLE_LIVE_TRADING=true
LIVE_TRADING_ACKNOWLEDGED=true
```

The risk manager will still reject any order that violates the limits
in `RISK_CONTROLS.md`.

---

## Strategies

Implemented in `app/stocks/strategies/` (registered in
`STRATEGY_REGISTRY`):

| Name                  | Idea                                                                 |
|-----------------------|----------------------------------------------------------------------|
| `stock_momentum`      | EMA9>EMA21, price above VWAP, RSI not overbought, volume surge, ATR stop. |
| `stock_mean_reversion`| Price below VWAP with oversold RSI; targets VWAP reversion.          |
| `stock_breakout`      | High-of-day breakout with relative-volume confirmation; avoids chasing far from VWAP. |
| `stock_pullback`      | Established uptrend, pullback to EMA21/VWAP with cooling RSI and bounce confirmation. |
| `stock_news_gated`    | Filter gate: only allows BUY when NLP/LLM sentiment is bullish.      |

Each strategy emits a `StockSignal` with `action`, `confidence`,
`suggested_price`, `stop_price`, and `rationale`. The decision engine
selects the highest-confidence non-HOLD signal as the L1 input.

---

## Three-layer decision engine

```
L1 — strategy signal (-1..+1, signed by direction × confidence)
L2 — ML probability of upward move (-1..+1)
L3 — local LLM sentiment × relevance (-1..+1)
        + bounded confidence_adjustment (±0.25)

final_score = L1*W1 + L2*W2 + L3*W3 + adjustment
```

Default weights (Jetson):

```
STOCK_L1_WEIGHT=0.50
STOCK_L2_WEIGHT=0.30
STOCK_L3_WEIGHT=0.20
STOCK_MIN_FINAL_SCORE=0.55
```

Trades are sent only when **all** of these are true:

* `final_score >= STOCK_MIN_FINAL_SCORE` (or matching short threshold).
* L1 signal exists and is non-HOLD.
* `LLMVerdict.should_gate_trade` is `false`.
* The market regime allows the requested direction.
* `StockRiskManager.check_order()` approves.

Every evaluation produces a `StockDecisionTrace` (visible at
`GET /api/decisions/recent`) showing every score and the exact
`blocked_reason` when a trade is rejected.

---

## Default ticker universe

```
SPY, QQQ, AAPL, MSFT, NVDA, TSLA, AMD, META, AMZN, GOOGL
```

The risk manager refuses any ticker outside `APPROVED_STOCK_TICKERS`.
Add more — but keep them highly liquid so slippage assumptions hold.
