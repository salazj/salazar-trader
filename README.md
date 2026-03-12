# $alazar-Trader

A multi-asset trading platform with a **web GUI**, supporting **prediction markets** (Polymarket, Kalshi) and **stocks** (Alpaca). Three intelligence layers — including **dual-LLM market analysis** (GPT-4o + Claude) and **multi-source news ingestion** — a FastAPI control backend, React dashboard, and Docker Compose orchestration.

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
./start.sh
```

This pulls the latest images, starts the backend + frontend, and tails the backend logs so you can see everything live. Press `Ctrl+C` to detach from the logs (containers keep running).

| Container | Port | Description |
|-----------|------|-------------|
| `salazar-backend` | 8000 | FastAPI server + BotManager |
| `salazar-frontend` | **3000** | React GUI (nginx reverse proxy) |

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
./stop.sh
```

Stops the containers but keeps them. Run `./start.sh` to restart.

### 5. Remove containers

```bash
./remove.sh
```

Stops and removes both containers and the Docker network. Optionally removes the images too. Run `./start.sh` to pull fresh and start again.

### 6. Update to latest version

```bash
./remove.sh      # tear down old containers
./start.sh       # pulls latest images and starts fresh
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
    │       ├── Broker Adapters (Alpaca)
    │       ├── News Ingestion (NewsAPI, RSS, Google News, Finnhub)
    │       └── Dual-LLM Market Analyzer (GPT-4o + Claude)
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
- The LLMs produce **structured signals only** — they never place trades directly

---

## Intelligence Layers

| Layer | Type | Description |
|-------|------|-------------|
| **Level 1** | Rule-based | Deterministic strategies (market maker, momentum, prediction value, sentiment) |
| **Level 2** | ML | Tabular classifiers (logistic regression, gradient boosting, random forest) |
| **Level 3** | NLP/AI | Multi-source news ingestion, LLM news classification, dual-LLM market analysis |

All layers produce `NormalizedSignal` objects that the ensemble evaluates with configurable weights, conflict detection, and veto logic. Every decision is fully traceable.

### L1 Strategies

| Strategy | Description |
|----------|-------------|
| `prediction_value` | Detects mean-reversion and momentum edge in the $0.20–$0.80 price range with volume filters |
| `passive_market_maker` | Places limit orders around the mid-price to capture the bid-ask spread |
| `momentum_scalper` | Detects short-term price momentum from orderbook imbalance |
| `event_probability_model` | ML-powered event probability estimation (L2) |
| `sentiment_adapter` | Translates NLP sentiment signals into trading signals |

### L3: News Ingestion

The bot pulls news from **multiple sources simultaneously** and uses them for both keyword-based NLP classification and LLM-powered analysis:

| Provider | Source | API Key Required |
|----------|--------|-----------------|
| `newsapi` | [NewsAPI.org](https://newsapi.org) — 80k+ sources | Yes (`NEWSAPI_KEY`) |
| `rss` | RSS/Atom feeds (Reuters, BBC, NPR, NYT, CNBC) | No |
| `google_news` | Google News RSS — multi-topic search | No |
| `finnhub` | [Finnhub.io](https://finnhub.io) — financial news | Yes (`FINNHUB_API_KEY`) |
| `mock` | Synthetic headlines for testing | No |
| `file` | Local text files from `data/news/` | No |

Configure active providers via comma-separated list:
```bash
NLP_PROVIDERS=newsapi,rss,google_news,finnhub
```

### L3: Dual-LLM Market Analysis (GPT-4o + Claude)

The bot runs two LLMs in parallel to evaluate prediction market mispricing:

```
Active Markets + News Headlines
        │
        ├──→ GPT-4o Analyzer ──→ ┐
        │                        ├──→ Compare confidence ──→ Pick winner per market
        └──→ Claude Analyzer ──→ ┘
```

Both models receive the same market question, current price, and relevant news. They estimate the true probability, identify edge, and return a direction (buy_yes / buy_no / hold). **Whichever model returns higher confidence wins** for each market.

The system works with one or both LLMs. If only GPT-4o is configured, it runs solo. If both are configured, they compete every cycle (180 seconds).

---

## NLP & LLM Configuration

Set in `.env`:

```bash
# --- News providers (comma-separated) ---
NLP_PROVIDERS=newsapi,rss,google_news,finnhub
NEWSAPI_KEY=your-newsapi-key
RSS_FEED_URLS=https://feeds.reuters.com/reuters/topNews,https://feeds.bbci.co.uk/news/rss.xml
FINNHUB_API_KEY=your-finnhub-key

# --- GPT-4o (primary LLM) ---
LLM_PROVIDER=hosted_api
LLM_MODEL_NAME=gpt-4o
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-openai-key

# --- Claude (second LLM, competitive mode) ---
CLAUDE_API_KEY=sk-ant-your-anthropic-key
CLAUDE_MODEL_NAME=claude-sonnet-4-6
```

All LLMs are optional. The bot works fully with keyword-only classification (`LLM_PROVIDER=none`). Claude is optional and activates automatically when `CLAUDE_API_KEY` is set.

### Ensemble Decision Engine

The decision engine combines signals from all three layers with configurable behavior:

| Setting | Default | Description |
|---------|---------|-------------|
| `DECISION_MODE` | `balanced` | `conservative`, `balanced`, or `aggressive` |
| `MIN_ENSEMBLE_CONFIDENCE` | `0.30` | Minimum confidence to execute a trade |
| `MIN_LAYERS_AGREE` | `2` | Number of layers that must agree on direction |
| `MIN_EVIDENCE_SIGNALS` | `2` | Minimum raw signals required |

---

## Docker Images

| Image | Source | Description |
|-------|--------|-------------|
| `salazj16/salazar-trader` | Docker Hub | Backend (FastAPI + bot) |
| `ghcr.io/salazj/salazar-trader-frontend` | GHCR | Frontend (React + nginx) |

Pull individually if needed:

```bash
docker pull salazj16/salazar-trader:latest
docker pull ghcr.io/salazj/salazar-trader-frontend:latest
```

---

## Docker Compose Services

| Service | Command | Profile | Purpose |
|---------|---------|---------|---------|
| `backend` | `api` | *(default)* | FastAPI server + BotManager, port 8000 |
| `frontend` | — | *(default)* | React GUI via nginx, port 3000 |
| `bot` | `bot` | `standalone` | CLI-only bot without API/GUI |
| `backtest` | `backtest` | `tools` | One-shot strategy backtesting |
| `train` | `train` | `tools` | One-shot ML model training |

---

## Convenience Scripts

Three scripts in the repo root handle the full container lifecycle:

| Script | What it does |
|--------|-------------|
| `./start.sh` | Pull latest images, start backend + frontend, tail backend logs |
| `./stop.sh` | Stop both containers (keeps them for quick restart) |
| `./remove.sh` | Stop, remove containers + network, optionally delete images |

```bash
./start.sh       # pull + run + show logs (Ctrl+C to detach)
./stop.sh        # pause
./start.sh       # restart (re-pulls latest)
./remove.sh      # full cleanup
```

---

## Building from Source (Docker Compose)

If you want to build the images yourself (e.g. you've modified the code or want to use your own `.env`), use the Docker Compose build override:

### 1. Clone and configure

```bash
git clone https://github.com/salazj/salazar-trader.git
cd salazar-trader
cp .env.example .env
# Edit .env with your API keys
```

### 2. Build images from source

```bash
# Build both backend and frontend from local source
docker compose -f docker-compose.yml -f docker-compose.build.yml build
```

This uses the root `Dockerfile` for the backend and `frontend/Dockerfile` for the frontend. Your local code is baked into the images.

### 3. Start

```bash
# Build and start in one command
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d
```

### 4. View logs

```bash
# Follow backend logs live
docker compose logs -f backend

# Last 100 lines
docker compose logs backend --tail 100
```

### 5. Stop / Rebuild

```bash
# Stop and remove containers
docker compose down

# Full teardown (containers + images + volumes)
docker compose down --rmi all --volumes --remove-orphans

# Rebuild from source after code changes
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d
```

### Build individual services

```bash
# Build only backend
docker compose -f docker-compose.yml -f docker-compose.build.yml build backend

# Build only frontend
docker compose -f docker-compose.yml -f docker-compose.build.yml build frontend
```

### Check container status

```bash
docker compose ps
```

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
  salazj16/salazar-trader:latest bot
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

## Risk Controls

Default limits (configurable via GUI or `.env`):

**Prediction Markets:**
- Max position per market: 5 contracts
- Max total exposure: $50
- Max daily loss: $10 (circuit breaker)
- Max 3 orders per minute
- Tradeable price range: $0.20–$0.80 (enforced on all strategies)
- Minimum 24h volume: 50 contracts
- Per-instrument cooldown: 5 minutes (market maker)

**Stocks:**
- Max position: $1,000
- Max portfolio: $10,000
- Max daily loss: $500
- Max 10 open positions
- Market hours enforcement

See [docs/RISK_CONTROLS.md](docs/RISK_CONTROLS.md) for complete documentation.

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

# Pull and launch
./start.sh

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
│   ├── data/           Orderbook, features, domain models
│   ├── strategies/     L1 strategies (market maker, momentum, prediction value, sentiment)
│   ├── research/       ML training pipeline (L2)
│   ├── nlp/
│   │   ├── providers/  News sources (NewsAPI, RSS, Google News, Finnhub, mock, file)
│   │   ├── pipeline.py NLP text-to-signal pipeline with hybrid classification
│   │   └── market_analyzer.py  Dual-LLM market analysis (GPT-4o + Claude)
│   ├── news/           News ingestion service, models
│   ├── decision/       Ensemble decision engine, signal registry, traces
│   ├── execution/      Exchange-agnostic order management
│   ├── risk/           Risk checks, circuit breaker
│   ├── portfolio/      Position tracking, PnL
│   ├── universe/       Dynamic market selection and filtering
│   ├── storage/        SQLite repository
│   ├── backtesting/    Offline strategy evaluation
│   ├── replay/         Session playback
│   ├── monitoring/     Structured logging, health endpoint, metrics
│   └── main.py         Bot orchestrator (intelligence loop, LLM loop, housekeeping)
├── frontend/           React + Vite + Tailwind web GUI
│   ├── src/
│   │   ├── pages/      Dashboard, Config, Logs, Portfolio, Risk
│   │   ├── components/ UI components (sidebar, toast, status badge)
│   │   ├── hooks/      WebSocket hooks for live data
│   │   └── api/        API client and TypeScript types
│   ├── Dockerfile      Multi-stage Node → nginx build
│   └── nginx.conf      Reverse proxy config
├── start.sh                 Pull latest images + start containers + tail logs
├── stop.sh                  Stop containers
├── remove.sh                Stop + remove containers + images
├── docker-compose.yml       Compose orchestration (pull from registry)
├── docker-compose.build.yml Compose override to build from source
├── Dockerfile               Backend image
├── docker/                  Entrypoint script
├── tests/                   Comprehensive test suite
└── docs/                    Architecture, API, GUI, deployment guides
```

---

## Environment Variables Reference

### Exchange / Broker Credentials

| Variable | Description |
|----------|-------------|
| `ASSET_CLASS` | `prediction_markets` or `equities` |
| `EXCHANGE` | `polymarket` or `kalshi` |
| `BROKER` | `alpaca` |
| `KALSHI_API_KEY` | Kalshi API key ID |
| `KALSHI_PRIVATE_KEY` | Kalshi RSA private key (newlines as `\n`) |
| `ALPACA_API_KEY` | Alpaca API key |
| `ALPACA_SECRET_KEY` | Alpaca secret key |
| `ALPACA_PAPER` | `true` for paper trading |

### News & NLP

| Variable | Description |
|----------|-------------|
| `NLP_PROVIDERS` | Comma-separated provider list (e.g., `newsapi,rss,google_news,finnhub`) |
| `NEWSAPI_KEY` | API key for [NewsAPI.org](https://newsapi.org) |
| `RSS_FEED_URLS` | Comma-separated RSS/Atom feed URLs |
| `FINNHUB_API_KEY` | API key for [Finnhub.io](https://finnhub.io) |
| `NEWS_POLL_INTERVAL` | Seconds between news fetches (default: 300) |

### LLM / AI

| Variable | Description |
|----------|-------------|
| `LLM_PROVIDER` | `none`, `local_open_source`, or `hosted_api` |
| `LLM_MODEL_NAME` | OpenAI model (e.g., `gpt-4o`) |
| `LLM_BASE_URL` | OpenAI-compatible API base URL |
| `LLM_API_KEY` | OpenAI API key |
| `CLAUDE_API_KEY` | Anthropic API key (enables Claude as second LLM) |
| `CLAUDE_MODEL_NAME` | Claude model (default: `claude-sonnet-4-6`) |
| `LLM_TIMEOUT_SECONDS` | API call timeout (default: 30) |
| `LLM_CONFIDENCE_THRESHOLD` | Min confidence for hybrid classifier (default: 0.5) |

### Trading Safety

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `true` | Simulate trades without real orders |
| `ENABLE_LIVE_TRADING` | `false` | Second safety gate for live trading |
| `LIVE_TRADING_ACKNOWLEDGED` | `false` | Third safety gate — explicit acknowledgment |

### Decision Engine

| Variable | Default | Description |
|----------|---------|-------------|
| `DECISION_MODE` | `balanced` | `conservative`, `balanced`, or `aggressive` |
| `MIN_ENSEMBLE_CONFIDENCE` | `0.30` | Minimum confidence to trade |
| `MIN_LAYERS_AGREE` | `2` | Required agreeing layers |
| `MIN_EVIDENCE_SIGNALS` | `2` | Minimum raw signals |

---

## Running Tests

```bash
# All tests
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
