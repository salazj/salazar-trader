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
cp .env.example .env
# edit .env: add ALPACA_API_KEY / ALPACA_SECRET_KEY

./start.sh            # interactive menu: Containerized or Native (see below)
```

`./start.sh` is the single entry point and offers two install paths — both
ship the **React web dashboard**:

| Path             | What runs                                              | GUI URL                  |
|------------------|--------------------------------------------------------|--------------------------|
| **Containerized**| Docker compose: `ollama` + backend + frontend (nginx)  | `http://<ip>:3000`       |
| **Native**       | Python venv + systemd service running the API+GUI server | `http://<ip>:8000`     |

In both paths, **trading is started from the dashboard** (the server does not
autostart trading). Stop everything with `./stop.sh`.

`start.sh` is **idempotent**: once installed, running `./start.sh` again
detects the existing install and simply starts it (it won't reinstall or
rebuild). `./stop.sh` likewise detects whether the container stack or the
native service is running and stops the right one.

The dashboard shows:

* Current mode (dry-run / paper / live), broker, account equity, buying power
* Active strategy, active tickers, open positions, daily PnL, win rate
* Current market regime (trending / range-bound / risk-off / etc.)
* Latest decisions with **L1 / L2 / L3** scores, LLM sentiment, blocked reasons
* Risk status, circuit-breaker state, emergency-stop button (confirm-required)

---

## Running 24/7 (native systemd)

`./start.sh native` sets this up for you (venv + systemd unit). The service
runs the **API + GUI server** (`python -m app.api`) and auto-starts on boot /
auto-restarts on crash. Trading itself is started from the dashboard, so after
a reboot open `http://<ip>:8000` and press **Start**.

To do it manually (or to understand what `start.sh native` does):

```bash
# 1. Verify Alpaca connectivity first
.venv/bin/python scripts/diagnose_alpaca.py

# 2. Make sure Ollama is running as a service (for the local LLM)
sudo systemctl enable --now ollama

# 3. Install the service (edit User / paths in the file if yours differ)
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

## Install paths (`start.sh`)

`./start.sh` with no arguments is **smart**: if Salazar Trader is already
installed it just starts it; otherwise it shows the install menu. You can also
run it non-interactively:

```bash
./start.sh                   # detect existing install and start it (or show menu)
./start.sh install           # always show the install menu

./start.sh docker            # containerized; build/pull only if images are missing
./start.sh docker --pull     # containerized, force pull prebuilt images from GHCR
./start.sh docker --build    # containerized, force build images from local source

./start.sh native            # native; (re)install only if needed, then start
./start.sh native --reinstall# native, force venv reinstall + systemd unit refresh

./stop.sh                    # stop whichever path is running (container or native)
```

How the smart start decides what to do:

| Situation                         | `./start.sh` (no args) does…                     |
|-----------------------------------|--------------------------------------------------|
| Nothing installed                 | shows the install menu                           |
| Native install present            | starts the systemd service (no reinstall)        |
| Container images/containers present | `docker compose up -d` (no rebuild/pull)       |
| Both present                      | asks which one to start                          |

### Containerized (Docker)

Runs three services via `docker compose`: a swappable **`ollama`** LLM
container, the **backend** (FastAPI on :8000), and the **frontend** (React via
nginx on :3000). The frontend proxies API/WS calls to the backend, and the
backend's LLM endpoints are pointed at the `ollama` container automatically
(overriding the `127.0.0.1` values in `.env`).

* **Pull prebuilt (GHCR):** fastest — uses `ghcr.io/salazj/salazar-trader` and
  `…-frontend`. (ARM64 images, see *Publishing* below.)
* **Build from source:** uses the local `Dockerfile` / `frontend/Dockerfile`
  via `docker-compose.build.yml`.

**Swapping the LLM model** (containerized): set `OLLAMA_MODEL` in `.env` (e.g.
`OLLAMA_MODEL=llama3.1`) and re-run `./start.sh docker`. The one-shot
`ollama-pull` service fetches the new model into the `ollama-models` volume and
the backend uses it. The Jetson GPU is used best-effort via the `nvidia`
runtime; if that runtime isn't installed, comment out the `runtime: nvidia`
line in `docker-compose.yml` to fall back to CPU.

### Native (systemd)

For Node-free hosts: the repo ships a **pre-built GUI bundle** in
`app/api/static/`, so the FastAPI server serves both the API and the dashboard
on :8000 — no nginx, no Node. `./start.sh native` creates a `.venv`,
`pip install -e .`, renders a systemd unit for the current path/user, and
enables it. Trading is started from the dashboard; after a reboot, open the
dashboard and press **Start**. This path expects Ollama to run as its own
host service (`sudo systemctl enable --now ollama`).

> Rebuilding the GUI bundle: when the frontend changes, rebuild and re-commit
> the bundle on a machine with Node:
> `cd frontend && npm install && npm run build && rm -rf ../app/api/static && cp -R dist/. ../app/api/static/`.

---

## Publishing images (ARM64 → GHCR)

`scripts/publish_images.sh` builds and pushes the backend and frontend images
for `linux/arm64` (Jetson / Apple Silicon target). You need a GitHub token with
`write:packages`:

```bash
export GHCR_USER=salazj
export GHCR_TOKEN=ghp_xxxxxxxx          # write:packages scope
./scripts/publish_images.sh             # builds + pushes :latest
TAG=v3.0.0 ./scripts/publish_images.sh  # tagged release
```

After publishing, any host can `./start.sh docker` (pull path) to run the
latest images without building locally.

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
| `start.sh` / `stop.sh`              | Unified launcher (containerized or native)    |
| `scripts/setup_jetson.sh`           | One-shot Jetson setup (deps, venv, Ollama)    |
| `scripts/diagnose_alpaca.py`        | Smoke-test Alpaca credentials + connectivity  |
| `scripts/logs.sh`                   | Friendly log viewer (live/errors/trades/news) |
| `scripts/publish_images.sh`         | Build + push ARM64 images to GHCR             |
| `scripts/backtest_stock_strategy.py`| Single-strategy + walk-forward backtests      |
| `deploy/salazar-trader.service`     | systemd unit for 24/7 operation               |
| `deploy/salazar-trader.logrotate`   | Log rotation (14 compressed days)             |

---

## Disclaimer

This software is for educational and research purposes only. Trading
stocks involves risk of loss. The system **does not guarantee profits**
and **is not financial advice**. Use at your own risk.
