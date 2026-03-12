# $alazar-Trader

A multi-asset trading platform with a **web GUI**, supporting **prediction markets** (Polymarket, Kalshi) and **stocks** (Alpaca). Three intelligence layers, a FastAPI control backend, React dashboard, and Docker Compose orchestration.

**This system does NOT promise profits.** It is designed to minimize mistakes, overtrading, and catastrophic losses through conservative defaults, comprehensive risk controls, and multiple operating modes.

| Asset Class | Exchange / Broker | Config |
|-------------|------------------|--------|
| Prediction Markets | Polymarket | `ASSET_CLASS=prediction_markets` `EXCHANGE=polymarket` |
| Prediction Markets | Kalshi | `ASSET_CLASS=prediction_markets` `EXCHANGE=kalshi` |
| Equities | Alpaca | `ASSET_CLASS=equities` `BROKER=alpaca` |

Pre-built images are available for **linux/amd64** and **linux/arm64** (Intel/AMD and Apple Silicon / ARM servers).

---

## Quick Start

### Prerequisites

- Docker 20.10+
- Docker Compose v2+

### 1. Get the config files

```bash
git clone https://github.com/salazj/salazar-trader.git
cd salazar-trader
cp .env.example .env
```

Edit `.env` with your API keys. At minimum, set credentials for one exchange/broker:

**For Kalshi:**
```
ASSET_CLASS=prediction_markets
EXCHANGE=kalshi
KALSHI_API_KEY=your-key-id
KALSHI_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----
```

**For Polymarket:**
```
ASSET_CLASS=prediction_markets
EXCHANGE=polymarket
PRIVATE_KEY=0x...
POLY_API_KEY=...
POLY_API_SECRET=...
POLY_PASSPHRASE=...
```

**For Stocks (Alpaca):**
```
ASSET_CLASS=equities
BROKER=alpaca
ALPACA_API_KEY=your-key
ALPACA_SECRET_KEY=your-secret
ALPACA_PAPER=true
```

### 2. Launch

```bash
docker compose up -d
```

Docker pulls the pre-built images from GHCR and starts two containers:

| Container | Image | Port | Description |
|-----------|-------|------|-------------|
| `salazar-backend` | `ghcr.io/salazj/salazar-trader` | 8000 | FastAPI server + BotManager |
| `salazar-frontend` | `ghcr.io/salazj/salazar-trader-frontend` | **3000** | React GUI (nginx reverse proxy) |

No build step required. Images are pulled automatically for your architecture (amd64 or arm64).

### 3. Open the GUI

Open **http://localhost:3000** in your browser.

From the GUI you can:
- Select a mode (Polymarket, Kalshi, or Stocks)
- Configure strategies, risk limits, NLP, and decision engine weights
- Start and stop the bot
- Watch live logs stream in real time
- Monitor positions, orders, P&L, and risk state
- Save and load configuration presets
- Trigger emergency stop

**The bot starts in dry-run mode by default. No real orders are placed until you explicitly enable live trading through the GUI safety gates.**

### 4. Stop

```bash
docker compose down
```

### 5. Update to latest version

```bash
docker compose pull
docker compose up -d
```

---

## How It Works

```
Browser (Desktop / Mobile)
    │
    ▼
nginx (frontend:3000)
    ├── /* → React static files
    ├── /api/* → proxy to backend:8000
    └── /ws/* → proxy to backend:8000 (WebSocket)

FastAPI Backend (backend:8000)
    ├── BotManager → TradingBot (async task)
    │       ├── Exchange Adapters (Polymarket, Kalshi)
    │       └── Broker Adapters (Alpaca)
    ├── REST API (status, config, portfolio, risk, logs)
    ├── WebSocket (live logs, live status, live portfolio)
    └── SQLite Repository
```

The GUI communicates **exclusively** through the control API. It never touches bot internals directly. All bot state is accessed through the `BotManager`, which encapsulates the trading bot lifecycle.

See [docs/API_REFERENCE.md](docs/API_REFERENCE.md) for the full API specification.

---

## Web GUI Pages

| Page | Path | What it does |
|------|------|-------------|
| Dashboard | `/` | Bot status, P&L, exposure, risk, positions, orders |
| Configuration | `/config` | Select mode, configure strategies/NLP/risk, start bot |
| Live Logs | `/logs` | Real-time log stream, filter, search, export |
| Portfolio | `/portfolio` | Positions, orders, fills, P&L history chart |
| Risk Controls | `/risk` | Circuit breaker, emergency stop, daily loss tracking |

See [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md) for a detailed page-by-page walkthrough.

---

## Safety Design

- **DRY_RUN=true** by default everywhere — Docker image, compose, code, and GUI
- Live trading requires **three explicit gates** to be opened:
  1. `DRY_RUN=false`
  2. `ENABLE_LIVE_TRADING=true`
  3. `LIVE_TRADING_ACKNOWLEDGED=true`
- The GUI shows a **red warning banner** on every page when live trading is active
- The GUI requires explicit toggle switches and confirmation before enabling live mode
- The backend validates credentials before allowing a live start
- Risk controls are always enforced and cannot be bypassed by AI layers
- No secrets are baked into the Docker image
- The LLM produces **structured signals only** — it never places trades

---

## Intelligence Layers

| Layer | Type | Description |
|-------|------|-------------|
| **Level 1** | Rule-based | Deterministic strategies (market maker, momentum, sentiment) |
| **Level 2** | ML | Tabular classifiers (logistic regression, gradient boosting, random forest) |
| **Level 3** | NLP/AI | Text classification, event understanding, optional LLM integration |

All layers produce `NormalizedSignal` objects that the ensemble evaluates with configurable weights, conflict detection, and veto logic. Every decision is fully traceable.

---

## Docker Images

| Image | Architectures | Tags |
|-------|---------------|------|
| `ghcr.io/salazj/salazar-trader` | `linux/amd64`, `linux/arm64` | `latest`, `3.0.0` |
| `ghcr.io/salazj/salazar-trader-frontend` | `linux/amd64`, `linux/arm64` | `latest`, `3.0.0` |

Pull individually if needed:

```bash
docker pull ghcr.io/salazj/salazar-trader:latest
docker pull ghcr.io/salazj/salazar-trader-frontend:latest
```

---

## Alternative: Build from Source

If you want to build the images locally instead of pulling from GHCR:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

This uses the `docker-compose.build.yml` override to build from the local `Dockerfile` and `frontend/Dockerfile`.

---

## Alternative: Standalone Bot (No GUI)

If you prefer CLI-only operation without the web GUI:

```bash
# Using Docker Compose with the standalone profile
docker compose --profile standalone up bot

# Or using the image directly
docker run -d --name salazar-trader \
  --restart unless-stopped \
  --env-file .env \
  -v ./data:/app/data \
  -v ./logs:/app/logs \
  -v ./model_artifacts:/app/model_artifacts \
  -v ./reports:/app/reports \
  -p 8880:8880 \
  ghcr.io/salazj/salazar-trader:latest bot
```

Note the `bot` command at the end — this runs the standalone bot without the API server.

---

## Alternative: Local Development (No Docker)

```bash
# Backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Start the API server
python -m app.api

# In another terminal — start the frontend dev server
cd frontend
npm install
npm run dev

# Open http://localhost:3000
```

---

## Toolbox Commands

```bash
# Backtest a strategy
docker compose run --rm --profile tools backtest --strategy momentum_scalper

# Train ML model
docker compose run --rm --profile tools train --synthetic

# Run tests locally
pytest -v --tb=short
```

---

## NLP & LLM Configuration

Set in `.env`:

```bash
# News provider: mock | newsapi | none
NLP_PROVIDER=newsapi
NEWSAPI_KEY=your-newsapi-key

# LLM provider: none | openai
LLM_PROVIDER=openai
LLM_MODEL_NAME=gpt-4o-mini
LLM_API_KEY=sk-your-openai-key
```

The LLM is optional. The bot works fully with keyword-only classification (`LLM_PROVIDER=none`).

---

## Risk Controls

Default limits (configurable via GUI or `.env`):

**Prediction Markets:**
- Max position per market: $10
- Max total exposure: $50
- Max daily loss: $10 (circuit breaker)
- Max 6 orders per minute

**Stocks:**
- Max position: $1,000
- Max portfolio: $10,000
- Max daily loss: $500
- Max 10 open positions
- Market hours enforcement

See [docs/RISK_CONTROLS.md](docs/RISK_CONTROLS.md) for complete documentation.

---

## Cloud / VM Deployment

```bash
# Install Docker on your VM
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Clone and configure
git clone https://github.com/salazj/salazar-trader.git
cd salazar-trader
cp .env.example .env
nano .env  # set your credentials

# Pull and launch (no build needed)
docker compose up -d

# Access the GUI
# http://<your-vm-ip>:3000
```

Works on any architecture (Intel, AMD, ARM, Apple Silicon VMs). The GUI is responsive and works on phone browsers. For HTTPS, put a reverse proxy (Caddy, nginx) in front of port 3000.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed deployment instructions.

---

## Persistent Volumes

| Path | Purpose |
|------|---------|
| `./data` | Market data, news files, presets, recorded sessions |
| `./logs` | Structured logs |
| `./model_artifacts` | Trained ML models |
| `./reports` | PnL reports, training reports |

These are mounted from the host. Data persists across container restarts.

---

## Project Structure

```
├── app/
│   ├── api/            FastAPI backend, BotManager, WebSocket endpoints
│   ├── config/         Settings, env loading, validation
│   ├── exchanges/      Exchange adapters (Polymarket, Kalshi)
│   ├── brokers/        Broker adapters (Alpaca)
│   ├── stocks/         Stock-specific strategies, features, risk, execution
│   ├── models/         Normalized cross-asset data models
│   ├── data/           Orderbook, features, domain models
│   ├── strategies/     Strategy interface + implementations (L1)
│   ├── research/       ML training pipeline (L2)
│   ├── nlp/            NLP classifiers, providers, pipeline (L3)
│   ├── decision/       Signal registry, ensemble, traces
│   ├── execution/      Exchange-agnostic order management
│   ├── risk/           Risk checks, circuit breaker
│   ├── portfolio/      Position tracking, PnL
│   ├── storage/        SQLite repository
│   ├── backtesting/    Offline strategy evaluation
│   ├── replay/         Session playback
│   ├── monitoring/     Logging, health endpoint
│   └── main.py         Bot orchestrator
├── frontend/           React + Vite + Tailwind web GUI
│   ├── src/
│   │   ├── pages/      Dashboard, Config, Logs, Portfolio, Risk
│   │   ├── components/ UI components (sidebar, toast, status badge)
│   │   ├── hooks/      WebSocket hooks for live data
│   │   └── api/        API client and TypeScript types
│   ├── Dockerfile      Multi-stage Node → nginx build
│   └── nginx.conf      Reverse proxy config
├── docker-compose.yml       Pull-and-run orchestration (uses GHCR images)
├── docker-compose.build.yml Override to build from source
├── Dockerfile               Backend image
├── docker/                  Entrypoint script
├── tests/                   Comprehensive test suite
└── docs/                    Architecture, API, GUI, deployment guides
```

---

## Running Tests

```bash
# All tests (105 tests)
pytest -v --tb=short

# API workflow tests only
pytest tests/test_api_workflow.py -v

# Safety gate tests
pytest tests/test_live_safety.py -v

# Stock component tests
pytest tests/test_stock_strategies.py tests/test_stock_risk.py -v
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Platform architecture and module responsibilities |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Complete API endpoint reference |
| [docs/GUI_GUIDE.md](docs/GUI_GUIDE.md) | Web GUI page-by-page walkthrough |
| [docs/STOCK_TRADING.md](docs/STOCK_TRADING.md) | Stock trading setup with Alpaca |
| [docs/RISK_CONTROLS.md](docs/RISK_CONTROLS.md) | Risk control documentation |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Local and cloud deployment guide |
| [docs/EXCHANGES.md](docs/EXCHANGES.md) | Exchange/broker setup details |

---

## Disclaimer

This software is for educational and research purposes only. Trading prediction markets and stocks involves risk of loss. This system does not guarantee profits and is not financial advice. Use at your own risk.
