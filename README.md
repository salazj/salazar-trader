# $alazar-Trader

A professional-grade multi-exchange prediction market trading system supporting **Polymarket** and **Kalshi**, with three intelligence layers, containerized for local development and cloud deployment.

**This system does NOT promise profits.** It is designed to minimize mistakes, overtrading, and catastrophic losses through conservative defaults, comprehensive risk controls, and multiple operating modes.

## Supported Exchanges

| Exchange | Status | Config |
|----------|--------|--------|
| **Polymarket** | Full support | `EXCHANGE=polymarket` |
| **Kalshi** | Full support | `EXCHANGE=kalshi` |

The architecture is exchange-agnostic at the core — strategies, decision engine, risk manager, and ML pipeline work identically on both exchanges.

## Safety Design

- **DRY_RUN=true** by default everywhere — Docker image, compose, and code
- Live trading requires **three explicit gates** to be opened:
  1. `DRY_RUN=false`
  2. `ENABLE_LIVE_TRADING=true`
  3. `LIVE_TRADING_ACKNOWLEDGED=true`
- No secrets are baked into the Docker image — safe to share publicly
- Risk controls are always enforced and cannot be bypassed by the AI layers
- The LLM produces **structured signals only** — it never places trades

## Architecture

Three intelligence layers feed a transparent decision engine:

| Layer | Type | Description |
|-------|------|-------------|
| **Level 1** | Rule-based | Deterministic strategies (market maker, momentum, sentiment) |
| **Level 2** | ML | Tabular classifiers (logistic regression, gradient boosting, random forest) |
| **Level 3** | NLP/AI | Text classification, event understanding, optional LLM integration |

All layers produce `NormalizedSignal` objects that the ensemble evaluates with configurable weights, conflict detection, and veto logic. Every decision is fully traceable.

```
app/
├── config/         Settings, env loading, validation
├── exchanges/      Exchange adapter layer
│   ├── base.py     Abstract interfaces (BaseExchangeAdapter, etc.)
│   ├── polymarket/ Polymarket REST, WS, execution adapters
│   └── kalshi/     Kalshi REST, WS, execution, auth adapters
├── clients/        Backward-compatibility shims
├── data/           Normalized models, orderbook, features
├── strategies/     Strategy interface + implementations (L1)
├── research/       ML training pipeline (L2)
├── nlp/            NLP classifiers, providers, pipeline (L3)
│   └── providers/  Mock, file, RSS, LLM adapters
├── decision/       Signal registry, ensemble, traces
├── execution/      Exchange-agnostic order management
├── risk/           Risk checks, circuit breaker
├── portfolio/      Position tracking, PnL
├── storage/        SQLite repository (exchange-aware schema)
├── backtesting/    Offline strategy evaluation
├── replay/         Session playback
├── monitoring/     Logging, health endpoint
└── main.py         Orchestrator (exchange adapter factory)
```

---

## Quick Start (Pre-Built Image)

The fastest way to deploy — no build step, no source code needed.

### Prerequisites

- Docker 20.10+

### 1. Set up

```bash
mkdir salazar-trader && cd salazar-trader
mkdir -p data logs model_artifacts reports

# Download the example env file
curl -sO https://raw.githubusercontent.com/salazj/salazar-trader/main/.env.example
cp .env.example .env
# Edit .env with your settings (all secrets go here)
```

### 2. Pull and run

```bash
docker pull ghcr.io/salazj/salazar-trader:latest

docker run -d --name salazar-trader \
  --env-file .env \
  -v ./data:/app/data \
  -v ./logs:/app/logs \
  -v ./model_artifacts:/app/model_artifacts \
  -v ./reports:/app/reports \
  -p 8880:8880 \
  ghcr.io/salazj/salazar-trader:latest
```

### 3. Check status

```bash
docker logs -f salazar-trader
curl http://localhost:8880/health
```

### 4. Stop

```bash
docker stop salazar-trader && docker rm salazar-trader
```

### 5. Update to latest version

```bash
docker pull ghcr.io/salazj/salazar-trader:latest
docker stop salazar-trader && docker rm salazar-trader
# Re-run the docker run command from step 2
```

---

## Quick Start (Docker Compose)

For running multiple services (backtest, replay, training) alongside the bot.

### Prerequisites

- Docker 20.10+
- Docker Compose v2+

### 1. Clone and configure

```bash
git clone https://github.com/salazj/salazar-trader.git
cd salazar-trader
cp .env.example .env
# Edit .env with your settings (all secrets go here, never in the image)
```

### 2. Run (pulls pre-built image automatically)

```bash
docker compose up bot
```

To build from source instead of pulling the image:

```bash
docker compose build
docker compose up bot
```

The bot starts with `DRY_RUN=true` and `ENABLE_LIVE_TRADING=false`. No real orders are placed.

### 3. Run other services

```bash
# Backtest a strategy
docker compose run --rm backtest --strategy momentum_scalper

# Replay a recorded session
docker compose run --rm replay --input /app/data/session.jsonl --strategy momentum_scalper

# Replay NLP examples
docker compose run --rm nlp-replay

# Train ML model
docker compose run --rm train --synthetic
```

### 4. View health

```bash
curl http://localhost:8880/health
curl http://localhost:8880/metrics
```

### 5. Stop

```bash
docker compose down
```

---

## Quick Start (Local Development)

### Prerequisites

- Python 3.11+
- pip or uv

### Installation

```bash
cd "PolyMarket Bot"
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python scripts/bootstrap_project.py
```

### Configuration

```bash
cp .env.example .env
# Edit .env — DRY_RUN=true is already the default
```

### Run

```bash
# Dry-run mode (safe)
python scripts/run_dry_bot.py

# Specific markets and strategy
python scripts/run_dry_bot.py -m will-something-happen -s passive_market_maker

# Backtest
python scripts/backtest_strategy.py --strategy momentum_scalper

# Train ML model
python scripts/train_model.py --synthetic

# Run tests
pytest
pytest -v --tb=short
pytest --cov=app
```

---

## Intelligence Layers

### Level 1: Rule-Based Strategies

Deterministic strategies that generate signals from orderbook features:

- **Passive Market Maker** — quotes around the spread
- **Momentum Scalper** — trades momentum with tight stops
- **Event Probability Model** — ML-based probability estimation
- **Sentiment Adapter** — converts NLP sentiment to trading signals

### Level 2: ML Prediction Models

Tabular classifiers trained on engineered features:

| Model | Type | Notes |
|-------|------|-------|
| `logistic_regression` | Linear | Fast, regularized, good baseline |
| `gradient_boosting` | Tree ensemble | Default choice, best on small data |
| `random_forest` | Tree ensemble | Parallel training, balanced classes |

Configure with `ML_MODEL_NAME` in `.env`. Train with:

```bash
# Docker
docker compose run --rm train --synthetic

# Local
python scripts/train_model.py --synthetic
python scripts/train_model.py --db salazar-trader.db --horizon 6
```

Model artifacts are persisted in `model_artifacts/`.

### Level 3: NLP / Event Understanding

Text classification pipeline with optional LLM integration:

```
Text → Normalizer → Classifier → Market Mapper → Structured Signal
```

**Classifiers** (composable, all run by default):
- `EventTypeClassifier` — election, crypto, legal, economic, etc.
- `SentimentClassifier` — bullish/bearish/neutral with negation awareness
- `UrgencyClassifier` — time-sensitivity scoring
- `RelevanceClassifier` — IDF-weighted relevance to specific markets
- `EntityExtractor` — proper nouns, acronyms, organizations

**Text providers** (`NLP_PROVIDER` setting):
- `mock` — hardcoded headlines for testing
- `file` — reads JSON from `data/news/`
- `rss` — RSS/Atom feeds (requires `feedparser`)
- `none` — disabled

---

## LLM Integration

The LLM is **optional** and **pluggable**. The bot is fully functional without it.

### Configuration

Set these in `.env`:

```bash
# Provider mode: none | local_open_source | hosted_api
LLM_PROVIDER=none

# Model name (passed to the API)
LLM_MODEL_NAME=meta-llama/Llama-3.1-8B-Instruct

# API endpoint
LLM_BASE_URL=http://localhost:8000/v1

# API key (optional for local models)
LLM_API_KEY=

# Timeout and confidence threshold
LLM_TIMEOUT_SECONDS=30
LLM_CONFIDENCE_THRESHOLD=0.5
```

### Provider Modes

| Mode | Description | Example |
|------|-------------|---------|
| `none` | No LLM, keyword classifiers only | Default, always works |
| `local_open_source` | Local model via OpenAI-compatible API | vLLM, Ollama, llama.cpp |
| `hosted_api` | Hosted API | OpenAI, Together, Groq, Anthropic |

### Example: Local Llama 3.1 with vLLM

```bash
# Terminal 1: Start vLLM server
pip install vllm
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000

# Terminal 2: Configure bot
LLM_PROVIDER=local_open_source
LLM_MODEL_NAME=meta-llama/Llama-3.1-8B-Instruct
LLM_BASE_URL=http://localhost:8000/v1
```

### Example: Local Mistral with Ollama

```bash
# Terminal 1: Start Ollama
ollama run mistral:7b-instruct-v0.3

# Terminal 2: Configure bot
LLM_PROVIDER=local_open_source
LLM_MODEL_NAME=mistral:7b-instruct-v0.3
LLM_BASE_URL=http://localhost:11434/v1
```

### Example: Hosted API (OpenAI)

```bash
LLM_PROVIDER=hosted_api
LLM_MODEL_NAME=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-key-here
```

### How the LLM is Used

The LLM is used **only** for structured classification:

1. **Headline relevance** — is this text relevant to a prediction market?
2. **Directional impact** — bullish, bearish, or neutral?
3. **Urgency** — how time-sensitive is this information?
4. **Event extraction** — what entities and event types are mentioned?

The LLM outputs a JSON object that is parsed into a `ClassificationResult`. When the `HybridClassifier` is active, it runs keyword classification first, then LLM. If the LLM confidence exceeds the threshold, its result is used; otherwise the keyword result stands.

**The LLM never:**
- Places orders
- Bypasses risk controls
- Makes execution decisions
- Accesses wallet keys

---

## Docker Commands Reference

### Pull Pre-Built Image

```bash
docker pull ghcr.io/salazj/salazar-trader:latest
```

### Build from Source (optional)

```bash
docker compose build
```

### Run Modes

```bash
# Dry-run (default, safe)
docker compose up bot

# Background
docker compose up -d bot

# Backtest
docker compose run --rm backtest --strategy passive_market_maker --fee-rate 0.01

# Replay session
docker compose run --rm replay --input /app/data/session.jsonl

# NLP replay
docker compose run --rm nlp-replay

# Train ML model
docker compose run --rm train --synthetic --n-samples 3000

# Interactive shell
docker compose run --rm bot shell
```

### Health Check

```bash
# From host
curl http://localhost:8880/health

# From inside container
docker compose exec bot /entrypoint.sh health
```

### Logs

```bash
docker compose logs -f bot
docker compose logs --tail 100 bot
```

### Stop and Clean

```bash
docker compose down
docker compose down -v  # also removes volumes
```

---

## Enabling Live Trading

Live trading is disabled by default and requires three explicit gates:

```bash
# In .env — ALL THREE must be set:
DRY_RUN=false
ENABLE_LIVE_TRADING=true
LIVE_TRADING_ACKNOWLEDGED=true
```

Plus valid credentials for your chosen exchange:

**Polymarket:**

```bash
EXCHANGE=polymarket
PRIVATE_KEY=0x...
POLY_API_KEY=...
POLY_API_SECRET=...
POLY_PASSPHRASE=...
```

**Kalshi:**

```bash
EXCHANGE=kalshi
KALSHI_API_KEY=your-kalshi-api-key
KALSHI_PRIVATE_KEY_PATH=~/.kalshi/private_key.pem
# Optional — for demo mode:
# KALSHI_DEMO_MODE=true
```

Then start the bot:

```bash
docker compose up bot
```

The startup banner will show `*** LIVE TRADING IS ENABLED ***` when all gates are open.

**WARNING:** Live trading involves real money. Start with very small `MAX_POSITION_PER_MARKET` and `MAX_TOTAL_EXPOSURE` values. Monitor closely. There is no guarantee of profit.

---

## Cloud Deployment

### Deploy to a VPS (Ubuntu / Oracle Linux / any Linux)

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 2. Set up
mkdir salazar-trader && cd salazar-trader
mkdir -p data logs model_artifacts reports

# 3. Configure
curl -sO https://raw.githubusercontent.com/salazj/salazar-trader/main/.env.example
cp .env.example .env
nano .env  # set your credentials and desired mode

# 4. Pull and start (no build needed)
docker pull ghcr.io/salazj/salazar-trader:latest
docker run -d --name salazar-trader \
  --restart unless-stopped \
  --env-file .env \
  -v ./data:/app/data \
  -v ./logs:/app/logs \
  -v ./model_artifacts:/app/model_artifacts \
  -v ./reports:/app/reports \
  -p 8880:8880 \
  ghcr.io/salazj/salazar-trader:latest

# 5. Monitor
docker logs -f salazar-trader
curl http://localhost:8880/health
```

### Systemd Service (Optional)

Create `/etc/systemd/system/salazar-trader.service`:

```ini
[Unit]
Description=Salazar Trader
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/user/salazar-trader
ExecStart=/usr/bin/docker compose up -d bot
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable salazar-trader
sudo systemctl start salazar-trader
sudo systemctl status salazar-trader
```

### Resource Requirements

- **Minimum:** 1 vCPU, 1 GB RAM (keyword classifiers only)
- **With local LLM:** 4+ vCPU, 8+ GB RAM (depends on model size)
- **Storage:** ~100 MB base + data accumulation

---

## Persistent Volumes

| Path | Purpose |
|------|---------|
| `./data` | Market data, news files, recorded sessions |
| `./logs` | Structured logs |
| `./model_artifacts` | Trained ML models |
| `./reports` | PnL reports, training reports |

These are mounted from the host into the container. Data persists across container restarts.

---

## Risk Controls

Default limits for a $100 bankroll (configurable in `.env`):

- Max position per market: $10
- Max total exposure: $50
- Max daily loss: $10 (triggers circuit breaker)
- Max 6 orders per minute
- Stale data lockout (30 seconds)
- High volatility lockout
- Emergency stop file (`touch EMERGENCY_STOP`)

See [docs/RISK_CONTROLS.md](docs/RISK_CONTROLS.md) for complete documentation.

---

## Running Tests

```bash
# Local — all tests (includes cross-exchange tests)
pytest
pytest -v --tb=short
pytest --cov=app

# Cross-exchange tests only (proves strategies work on both Polymarket and Kalshi data)
pytest tests/test_exchange_agnostic.py -v

# Kalshi adapter tests only
pytest tests/test_kalshi_adapter.py -v

# Docker
docker compose run --rm bot shell -c "pip install -e '.[dev]' && pytest"
```

---

## Supplying Your Own Credentials

1. Copy `.env.example` to `.env`
2. Set `EXCHANGE=polymarket` or `EXCHANGE=kalshi`
3. Fill in credentials for your chosen exchange:
   - **Polymarket** — API key, secret, passphrase, private key (see [docs/EXCHANGES.md](docs/EXCHANGES.md))
   - **Kalshi** — API key + RSA private key PEM file (see [docs/EXCHANGES.md](docs/EXCHANGES.md))
4. Credentials are **never** included in the Docker image
5. The `.env` file is excluded from git via `.gitignore`
6. All secrets are redacted from logs and `repr()` output

---

## Disclaimer

This software is for educational and research purposes only. Trading prediction markets involves risk of loss. This system does not guarantee profits and is not financial advice. Use at your own risk.
