# Architecture

## Overview

$alazar-Trader is a multi-asset trading platform with a web GUI. The architecture has five major layers:

1. **Trading Core** — data ingestion, features, strategies, ML, NLP, decision engine, risk, execution, portfolio
2. **Exchange/Broker Adapters** — Polymarket (prediction markets), Alpaca (stocks)
3. **Control API Backend** — FastAPI server managing the bot lifecycle, config, and streaming
4. **Web GUI Frontend** — React dashboard with real-time updates via WebSocket
5. **Docker Orchestration** — Docker Compose with backend, frontend, and optional standalone bot

### Platform Architecture

```
Browser (Desktop/Mobile)
    │
    ▼
nginx (frontend container, port 3000)
    ├── /* → React static files
    ├── /api/* → proxy to backend:8000
    └── /ws/* → proxy to backend:8000 (WebSocket)

FastAPI Backend (port 8000)
    ├── BotManager → TradingBot (async task)
    │       ├── Exchange Adapters (Polymarket)
    │       └── Broker Adapters (Alpaca)
    ├── REST endpoints (status, config, portfolio, risk)
    ├── WebSocket endpoints (logs, status, portfolio)
    └── SQLite Repository
```

The bot uses **three coordinated intelligence layers** for decision-making, combined through a transparent ensemble decision engine, with deterministic risk controls that can never be overridden by AI.

## Three-Layer Intelligence Flow

```
Market Data (REST/WS)    News / Events
    │                        │
    ▼                        ▼
┌──────────┐          ┌──────────────┐
│ Clients  │          │    News      │
│ (REST/WS)│          │  Ingestion   │
└────┬─────┘          └──────┬───────┘
     │                       │
     ▼                       ▼
┌──────────┐          ┌──────────────┐
│ Orderbook│          │  NLP Pipeline│
│ Manager  │          │ (Classify +  │
└────┬─────┘          │  Map Markets)│
     │                └──────┬───────┘
     ▼                       │
┌──────────────┐             │
│   Feature    │             │
│   Engine     │             │
└──────┬───────┘             │
       │                     │
       ├──────┬──────────────┘
       │      │
       ▼      ▼
┌─────────────────────────────────────┐
│     Three Intelligence Layers       │
│                                     │
│  L1: Rule Strategies  (deterministic│
│  L2: ML Prediction    (tabular ML)  │
│  L3: NLP/Event AI     (text-based)  │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│     Decision Engine (Ensemble)      │
│                                     │
│  Signal normalization               │
│  Weighted aggregation               │
│  Agreement gating (N-of-3)          │
│  Confidence thresholding            │
│  L3 gate/suppress                   │
│  Conflict resolution                │
│  → TradeCandidate + DecisionTrace   │
└────────────┬────────────────────────┘
             │
             ▼
┌──────────────┐    ┌──────────┐
│  Execution   │◀──▶│   Risk   │
│   Engine     │    │  Manager │
└──────┬───────┘    └──────────┘
       │            (deterministic,
       │             never AI-controlled)
       ▼
┌──────────────┐    ┌──────────┐
│   Trading    │───▶│Portfolio │
│   Client     │    │ Tracker  │
└──────────────┘    └──────────┘
       │
       ▼
┌──────────────┐
│   Storage    │
│ (SQLite/PG)  │
└──────────────┘
```

## Module Responsibilities

### config/
Loads all settings from environment variables via pydantic-settings. Validates ranges, provides defaults, and supports dev/test/prod environments. Includes decision engine weights, NLP provider selection, and three safety gates for live trading.

### clients/
- **REST client**: Market metadata, orderbook snapshots, midpoints. Uses httpx with retry/backoff.
- **WebSocket client**: Real-time book updates and trades. Auto-reconnects with exponential backoff, heartbeat monitoring, stale detection.
- **Trading client**: Order placement/cancellation. Supports dry-run (simulated) and live modes behind the same interface.

### data/
- **models.py**: All domain types (Market, Order, Signal, Position, etc.) as pydantic models.
- **orderbook.py**: Thread-safe in-memory orderbook manager. Applies snapshots and deltas.
- **features.py**: Stateful feature engine computing microprice, imbalance, momentum, volatility, trade flow, and depth metrics.
- **providers/**: Abstract data provider interfaces with sentiment and news stubs.

### decision/
- **signals.py**: `LayeredSignal`, `TradeCandidate`, `DecisionTrace`, `IntelligenceLayer` enum.
- **ensemble.py**: Weighted score aggregation, agreement gating, L3 gate/suppress, decision modes (conservative/balanced/aggressive).
- **engine.py**: `DecisionEngine` — orchestrates evaluation, produces auditable candidates and traces.

### nlp/
- **signals.py**: `NlpSignal`, `EventType`, `SentimentDirection`, `ClassificationResult`.
- **classifier.py**: `KeywordClassifier` (regex baseline), `LlmClassifierAdapter` (stub for external LLMs).
- **market_mapper.py**: Links text to candidate Polymarket markets via keyword/entity overlap scoring.
- **pipeline.py**: Orchestrates classify → map → generate NLP signals.
- **providers/**: `MockProvider`, `FileProvider`, `RssProvider` (stub).

### news/
- **models.py**: `NewsItem` data model.
- **ingestion.py**: `NewsIngestionService` — polls providers, deduplicates, caches, feeds into NLP pipeline.

### strategies/
Abstract base with `generate_signal()` interface. Strategies only produce Signal objects — they never place orders directly. Registry pattern for dynamic strategy selection. Four strategies: passive market maker, momentum scalper, event probability model (ML), sentiment adapter.

### execution/
Converts signals to orders with safety checks: price validation, size validation, deduplication, risk gate. Manages order lifecycle state machine (pending → acknowledged → filled/canceled/rejected).

### risk/
13+ independent pre-trade checks that ALL must pass. Circuit breaker kill switch with auto-cancel. Emergency stop file. Cash sufficiency check. Thread-safe counters for frequency and loss tracking. **Risk is always deterministic and never controlled by AI.**

### portfolio/
Tracks cash, positions, entry prices, realized/unrealized PnL. Correctly handles weighted average entry for incremental fills. Mark-to-market from current prices. Position persistence and startup recovery.

### storage/
Async SQLite repository with explicit schema and batched writes. Saves all system state: markets, raw events, features, signals, orders, fills, PnL snapshots, positions.

### backtesting/
- `BacktestEngine`: Single-strategy backtest with simulated fills, fees, slippage.
- `MultiLayerBacktestEngine`: Three-layer backtest through decision engine with per-layer PnL tracking. Supports selectively enabling layers.

### monitoring/
Structured logging via structlog. Thread-safe metric counters. HTTP health endpoint. Decision trace logging.

## Design Decisions

1. **Three-layer ensemble**: Signals from rules, ML, and NLP are combined transparently — not a black box.
2. **Risk stays deterministic**: No AI can override or weaken risk controls.
3. **Limit orders only**: Market orders are disabled to prevent slippage accidents.
4. **Strategy-execution separation**: Strategies never touch the order book directly.
5. **Risk checks are mandatory**: No bypass path exists in the execution engine.
6. **Dry-run is default**: Three safety gates required for live trading.
7. **Thread safety**: All shared state uses locks for concurrent access.
8. **Async I/O**: Network operations are async; strategy computation is synchronous.
9. **Pydantic models**: Strong typing and validation at data boundaries.
10. **Pluggable providers**: NLP/news providers are abstract — swap without rewriting the bot.

## Extension Points

- **New strategies**: Implement `BaseStrategy`, register with `@StrategyRegistry.register`.
- **New NLP providers**: Implement `BaseNlpProvider` and register with `NewsIngestionService`.
- **New classifiers**: Implement `BaseClassifier` (e.g., LLM-based via `LlmClassifierAdapter`).
- **Cloud deployment**: Docker-ready, environment-variable configured.
- **Dashboard**: Expose `metrics.snapshot()` and decision traces via HTTP endpoint.
- **Alerts**: Hook into monitoring logger for Telegram/Discord notifications.
