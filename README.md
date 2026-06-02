# Salazar-Trader — NVIDIA Jetson Orin Nano AI Stock Bot

> This project targets **NVIDIA Jetson Orin Nano** for local AI-assisted
> stock/ETF trading using **Alpaca**. It uses deterministic risk
> controls, technical strategies, optional ML prediction, and a local
> LLM/NLP sentiment filter. **It does not target Raspberry Pi, Pi AI
> HAT, or generic low-power hardware.**

The AI improves trade filtering and decision quality, but it can never
bypass deterministic risk controls. Profitability comes from disciplined
strategy, risk management, backtesting, and signal quality — not blind
LLM guessing.

---

## What it is

A FastAPI + React stock trading bot designed to run **end-to-end on a
single Jetson Orin Nano Super Developer Kit**:

* **Broker** — Alpaca (paper and live)
* **Asset class** — stocks and ETFs (highly liquid tickers only)
* **Decision engine** — three layers, deterministic risk-gated
  * **L1**: technical strategies (momentum, mean reversion, breakout,
    pullback, news-gated)
  * **L2**: tabular ML (sklearn baseline; auto-uses XGBoost/LightGBM
    if installed)
  * **L3**: local LLM via `llama.cpp` (CUDA on Jetson) or Ollama —
    sentiment + risk veto only
* **Risk manager** — 14 deterministic checks; the LLM cannot override
* **Backtester** — single-strategy and chronological walk-forward
* **Frontend** — React dashboard (mode, scores, regime, decisions,
  PnL, blocked trades, emergency stop with confirm)

---

## Default ticker universe

```
SPY  QQQ  AAPL  MSFT  NVDA  TSLA  AMD  META  AMZN  GOOGL
```

The risk manager rejects any ticker outside `APPROVED_STOCK_TICKERS`.

---

## Beginner-safe defaults

Tuned for paper trading on a small account:

| Limit                      | Default |
|----------------------------|--------:|
| Max position notional      | **$50** |
| Max portfolio exposure     | **$250**|
| Max daily loss             | **$25** |
| Max open positions         | **3**   |
| Max trades / day           | **5**   |
| Max orders / minute        | **3**   |
| Stop loss required         | **yes** |
| Extended-hours trading     | **no**  |

Three live-trading gates must all be flipped before any real order:

```
DRY_RUN=false
ENABLE_LIVE_TRADING=true
LIVE_TRADING_ACKNOWLEDGED=true
```

Plus valid Alpaca credentials. See `docs/RISK_CONTROLS.md`.

---

## Quick start (Jetson Orin Nano)

```bash
git clone https://github.com/salazj/salazar-trader.git
cd salazar-trader
bash scripts/setup_jetson.sh         # see docs/JETSON_DEPLOYMENT.md

cp .env.example .env
# edit .env: add ALPACA_API_KEY / ALPACA_SECRET_KEY

source .venv/bin/activate
python -m app.api                     # FastAPI on :8000
```

Then open the dashboard at `http://<jetson-ip>:3000`.

The dashboard shows:

* Current mode (dry-run / paper / live), broker, account equity, buying power
* Active strategy, active tickers, open positions, daily PnL, win rate
* Current market regime (trending / range-bound / risk-off / etc.)
* Latest decisions with **L1 / L2 / L3** scores, LLM sentiment, blocked reasons
* Risk status, circuit-breaker state, emergency-stop button (confirm-required)

---

## Running headless 24/7 (systemd)

For unattended operation on the Jetson, run the bot directly (no Docker)
as a systemd service. This auto-starts on boot and auto-restarts on crash.

```bash
# 1. Verify Alpaca connectivity first
.venv/bin/python scripts/diagnose_alpaca.py

# 2. Make sure Ollama is running as a service (for the local LLM)
sudo systemctl enable --now ollama

# 3. Install the bot service (edit paths in the file if yours differ)
sudo cp deploy/salazar-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now salazar-trader

# 4. Install log rotation (keeps 14 compressed days)
sudo cp deploy/salazar-trader.logrotate /etc/logrotate.d/salazar-trader
```

Service control:

| Task                       | Command                                   |
|----------------------------|-------------------------------------------|
| Status                     | `sudo systemctl status salazar-trader`    |
| Start / Stop               | `sudo systemctl start\|stop salazar-trader` |
| Restart (after `git pull`) | `sudo systemctl restart salazar-trader`   |
| Disable auto-start         | `sudo systemctl disable salazar-trader`   |

Updating to the latest code:

```bash
cd ~/salazar-trader
git pull
.venv/bin/pip install -e . --upgrade   # only if pyproject.toml changed
sudo systemctl restart salazar-trader
```

---

## Viewing logs

The service writes everything to `/var/log/salazar-trader.log`. The
`scripts/logs.sh` helper wraps `journalctl`/`tail` with simple commands:

```bash
./scripts/logs.sh            # live tail (the everyday one)
./scripts/logs.sh tail 500   # last 500 lines, no follow
./scripts/logs.sh errors     # only warnings/errors (live)
./scripts/logs.sh trades     # only orders/decisions/fills (live)
./scripts/logs.sh news       # only news + LLM + universe activity (live)
./scripts/logs.sh today      # everything logged today
./scripts/logs.sh status     # systemd service status
./scripts/logs.sh help       # full command list
```

Optional shortcut from anywhere:

```bash
echo "alias strader-logs='cd ~/salazar-trader && ./scripts/logs.sh'" >> ~/.bashrc
source ~/.bashrc
# then: strader-logs   (or  strader-logs trades)
```

---

## Alternative: Docker stack (with web dashboard)

`start.sh` / `stop.sh` run a **containerized** backend (FastAPI :8000) +
React frontend (:3000) via `docker compose`. This is an alternative to
the headless systemd path above — use it if you want the web dashboard.
Note it does not use the host's systemd service or the friendly log
viewer, and containers need extra config to reach a host-side Ollama.

```bash
./start.sh    # build images, start backend + frontend, tail backend logs
./stop.sh     # stop and remove the containers
```

---

## Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │              FastAPI + BotManager           │
                    │  /api/{status,health,config,bot,portfolio,  │
                    │       risk,decisions,llm,regime,backtests,  │
                    │       performance}                          │
                    └────────────────┬────────────────────────────┘
                                     │
                    ┌────────────────┴────────────────────────────┐
                    │                TradingBot                   │
                    │                                             │
                    │   features ─►  L1 strategies (momentum,…)   │
                    │                                             │
                    │             +  L2 StockMLPredictor          │
                    │                                             │
                    │             +  L3 LocalLLMService           │
                    │                                             │
                    │   regime ─►   StockDecisionEngine ─► trace  │
                    │                                             │
                    │                StockRiskManager (gate)      │
                    │                                             │
                    │                StockExecutionEngine ─► Alpaca│
                    └─────────────────────────────────────────────┘
```

* `app/llm/` — local LLM provider, strict JSON schema, TTL cache,
  fail-safe defaults.
* `app/regime/` — market regime classifier (SPY/QQQ trend, ATR, VIX).
* `app/stocks/` — features, strategies, risk, decision, ML, backtester.
* `app/brokers/alpaca/` — Alpaca adapter (market data, execution,
  streaming, market hours).

---

## API endpoints

```
GET    /api/health
GET    /api/status
GET    /api/config
POST   /api/config/validate
POST   /api/bot/start
POST   /api/bot/stop

GET    /api/portfolio
GET    /api/portfolio/positions
GET    /api/portfolio/orders
GET    /api/portfolio/fills

GET    /api/risk/status
POST   /api/risk/emergency-stop      # body: {"confirm": true}
POST   /api/risk/reset-circuit-breaker

GET    /api/decisions/recent
GET    /api/regime/current
GET    /api/performance/summary

GET    /api/llm/status
POST   /api/llm/test                 # quick prompt round-trip

GET    /api/backtests
POST   /api/backtests/run
```

---

## Backtesting

```bash
# Single strategy run
python scripts/backtest_stock_strategy.py \
  --strategy stock_momentum \
  --tickers SPY,QQQ,NVDA \
  --start 2024-01-01 --end 2024-12-31

# Walk-forward validation
python scripts/backtest_stock_strategy.py \
  --strategy stock_pullback \
  --tickers SPY,QQQ \
  --walk-forward --train-size 1500 --test-size 250
```

See `docs/BACKTESTING.md` for the data format and walk-forward details.

---

## Tests

```bash
source .venv/bin/activate
pytest -q
```

The Jetson-specific suites: `test_stock_*`, `test_llm_*`, `test_regime`,
`test_decision_engine_stock`, `test_walk_forward`. Other suites cover
the legacy multi-asset components which remain in the repo for
backwards compatibility.

---

## Documentation

| File                              | Topic                                          |
|-----------------------------------|------------------------------------------------|
| `docs/JETSON_DEPLOYMENT.md`       | Hardware, setup, performance modes             |
| `docs/STOCK_TRADING.md`           | Alpaca + strategies + decision engine          |
| `docs/LOCAL_LLM.md`               | Local LLM, JSON schema, providers              |
| `docs/BACKTESTING.md`             | Backtester + walk-forward CLI                  |
| `docs/RISK_CONTROLS.md`           | Every deterministic gate, kill-switch          |
| `docs/API_REFERENCE.md`           | Full REST API surface                          |
| `docs/GUI_GUIDE.md`               | Dashboard walkthrough                          |

### Helper scripts

| Script                              | Purpose                                       |
|-------------------------------------|-----------------------------------------------|
| `scripts/setup_jetson.sh`           | One-shot Jetson setup (deps, venv, Ollama)    |
| `scripts/diagnose_alpaca.py`        | Smoke-test Alpaca credentials + connectivity  |
| `scripts/logs.sh`                   | Friendly log viewer (live/errors/trades/news) |
| `scripts/backtest_stock_strategy.py`| Single-strategy + walk-forward backtests      |
| `deploy/salazar-trader.service`     | systemd unit for 24/7 operation               |
| `deploy/salazar-trader.logrotate`   | Log rotation (14 compressed days)             |

---

## Disclaimer

This software is for educational and research purposes only. Trading
stocks involves risk of loss. The system **does not guarantee profits**
and **is not financial advice**. Use at your own risk.
