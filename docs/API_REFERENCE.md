# Control API Reference

The $alazar-Trader backend exposes a RESTful API and WebSocket endpoints that the web GUI consumes. **All bot interaction flows through this API — the GUI never touches bot internals directly.**

Base URL: `http://localhost:8000` (proxied through nginx at port 3000 in Docker)

---

## Architecture Principles

1. **BotManager as boundary** — Every request to read bot state or control its lifecycle passes through the `BotManager` class. Route handlers never access `_bot`, `_repository`, or `_risk_manager` directly.
2. **Session tracking** — Each bot start assigns a unique `session_id` (12-char hex). This appears in status responses and logs, enabling future multi-session support.
3. **Validation before execution** — The `/api/config/validate` endpoint runs the same validation as `/api/bot/start`. The GUI validates before starting.
4. **Dry-run default** — The API enforces that live trading requires three explicit gates plus valid credentials.
5. **Dual log access** — Logs are available via REST (`GET /api/logs`) for polling and via WebSocket (`/ws/logs`) for streaming.

---

## REST Endpoints

### Status & Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Platform health check |
| `GET` | `/api/status` | Current bot session status |
| `GET` | `/api/logs` | Fetch recent log entries |

### Stock Decision Engine (Jetson)

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/decisions/recent?limit=N` | Last N `StockDecisionTrace` items (newest first). Each has `l1_score`, `l2_score`, `l3_score`, `final_score`, `action` (`buy/sell/hold/blocked`), `risk_checks`, `blocked_reason`, `explanation`, `weights`, `regime`, `llm_sentiment`, `llm_should_gate`, `ml_probability_up`. |
| `GET`  | `/api/regime/current` | Current market regime: `trending_bullish`, `trending_bearish`, `range_bound`, `high_volatility`, `low_volatility`, or `risk_off`. Includes `confidence`, `score`, `spy_trend`, `qqq_trend`, `atr_pct`, `vix`, `allows_long`. |
| `GET`  | `/api/performance/summary` | Rolling PnL summary: total_trades, wins, losses, total_pnl, win_rate, average_win, average_loss, profit_factor, max_drawdown, sharpe, by_strategy. |
| `GET`  | `/api/llm/status` | Local LLM provider info, call counters, cache stats. |
| `POST` | `/api/llm/test` | Body: `{ticker, technical_context, news_context}`. Returns the validated `LLMVerdict`. |
| `GET`  | `/api/backtests` | List recent backtest reports under `reports/`. |
| `POST` | `/api/backtests/run` | Body: `{strategy, tickers, start, end, walk_forward, train_size, test_size, use_synthetic, …}`. |

### Risk Controls

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/risk/status` | Current risk state (alias of `GET /api/risk`). |
| `POST` | `/api/risk/emergency-stop` | Body: `{"confirm": true}` — required. |
| `POST` | `/api/risk/reset-circuit-breaker` | Clear a tripped circuit breaker. |

#### `GET /api/health`

Returns platform health with version, session info, and subscriber count.

```json
{
  "status": "ok",
  "version": "3.0.0",
  "bot_running": false,
  "session_id": "",
  "asset_class": "",
  "mode": "dry-run",
  "uptime_seconds": 0.0,
  "log_subscribers": 0,
  "timestamp": "2026-03-11T20:00:00+00:00"
}
```

`status` is `"ok"` when healthy or `"degraded"` when an error has occurred.

#### `GET /api/status`

Returns the full bot session status including `session_id`.

```json
{
  "running": true,
  "status": "running",
  "session_id": "a1b2c3d4e5f6",
  "asset_class": "prediction_markets",
  "exchange": "kalshi",
  "broker": "",
  "mode": "dry-run",
  "dry_run": true,
  "live_trading": false,
  "uptime_seconds": 142.3,
  "error": null,
  "started_at": "2026-03-11T20:00:00Z"
}
```

#### `GET /api/logs`

Query params: `limit` (1–5000, default 200), `level` (debug/info/warning/error, default info).

Returns recent log entries from the ring buffer. Filters by minimum severity level.

```json
[
  {
    "timestamp": "2026-03-11T20:01:00",
    "level": "info",
    "event": "bot_session_started",
    "logger": "app.api.bot_manager",
    "data": {"session_id": "a1b2c3d4e5f6", "asset_class": "prediction_markets"}
  }
]
```

---

### Bot Control

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/bot/start` | Start a bot session |
| `POST` | `/api/bot/stop` | Stop the current session |
| `POST` | `/api/bot/restart` | Stop then start |

#### `POST /api/bot/start`

Body: `RunConfig` JSON object. Validates config before starting.

Responses:
- `200` — Bot started, returns `BotStatusResponse`
- `409` — Bot is already running
- `422` — Config validation failed (detail contains error messages)

#### `POST /api/bot/stop`

No body required. Always returns `200` with the new status (even if bot was already stopped).

#### `POST /api/bot/restart`

Optional body: `RunConfig` JSON. If omitted, reuses the last config.

---

### Configuration

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/config` | Get current/default config |
| `POST` | `/api/config/validate` | Validate a config without starting |
| `GET` | `/api/config/presets` | List saved presets |
| `POST` | `/api/config/presets/{name}` | Save a preset |
| `DELETE` | `/api/config/presets/{name}` | Delete a preset |

#### `POST /api/config/validate`

Body: `RunConfig` JSON.

Returns a `ValidationResult`:

```json
{
  "valid": false,
  "errors": ["Invalid asset_class: futures"],
  "warnings": []
}
```

Validation rules:
- `asset_class` must be `prediction_markets` or `equities`
- `exchange` must be `polymarket` or `kalshi` (when asset_class is PM)
- `broker` must be `alpaca` (when asset_class is equities)
- `decision_mode` must be `conservative`, `balanced`, or `aggressive`
- `max_daily_loss` and `max_total_exposure` must be positive
- Ensemble weight sum far from 1.0 generates a warning
- Live trading requires `enable_live_trading` + `live_trading_acknowledged` + valid credentials

---

### Portfolio

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/portfolio` | Full portfolio snapshot |
| `GET` | `/api/portfolio/positions` | Open positions |
| `GET` | `/api/portfolio/orders?limit=N` | Recent orders |
| `GET` | `/api/portfolio/fills?limit=N` | Recent fills |
| `GET` | `/api/portfolio/pnl-history?limit=N` | PnL time series |

All return empty arrays/defaults when the bot is not running.

---

### Risk

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/risk` | Current risk state |
| `POST` | `/api/risk/reset-breaker` | Reset circuit breaker |
| `POST` | `/api/risk/emergency-stop` | Trigger emergency stop |

`reset-breaker` and `emergency-stop` return `409` if the bot is not running.

---

### Exchanges & Strategies

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/exchanges` | List supported exchanges/brokers |
| `GET` | `/api/strategies` | List available strategies |

These return static lists. Exchanges include `config_fields` for reference. Strategies include `asset_class` for filtering.

---

## WebSocket Endpoints

### `/ws/logs?level=info`

Streams log entries in real time. On connect, sends the 200 most recent entries, then streams new entries as they arrive.

Each message is a JSON object:

```json
{"timestamp": "...", "level": "info", "event": "...", "logger": "...", "data": {}}
```

### `/ws/status`

Pushes bot status + risk state every 2 seconds:

```json
{
  "type": "status",
  "bot": { "running": true, "session_id": "...", ... },
  "risk": { "halted": false, ... }
}
```

### `/ws/portfolio`

Pushes portfolio + recent orders every 5 seconds:

```json
{
  "type": "portfolio",
  "portfolio": { "cash": 100.0, "positions": [...], ... },
  "recent_orders": [...]
}
```

---

## GUI Workflow

The web GUI follows this sequence:

```
1. GET  /api/health          → verify backend is reachable
2. WS   /ws/status           → start receiving live status
3. WS   /ws/portfolio        → start receiving portfolio updates
4. GET  /api/config           → load current/default config
5. GET  /api/strategies       → populate strategy checkboxes
6. GET  /api/exchanges        → populate exchange/broker options
7. User edits config in GUI
8. POST /api/config/validate  → validate before starting
9. POST /api/bot/start        → start the bot session
10. WS  /ws/logs?level=info   → stream live logs
11. GET /api/logs             → (optional) fetch log history on page load
12. GET /api/portfolio        → (optional) REST fallback for portfolio
13. POST /api/bot/stop        → stop the bot session
```

The GUI never modifies `.env` files, never accesses the filesystem, and never directly imports any bot module. All interaction is through this API.

---

## Live Trading Safety

The API enforces these gates before allowing live orders:

1. `dry_run: false` in the RunConfig
2. `enable_live_trading: true` in the RunConfig
3. `live_trading_acknowledged: true` in the RunConfig
4. Valid exchange/broker credentials present in the server environment
5. Risk parameters pass validation (positive limits)

If any gate fails, `/api/bot/start` returns `422` with a clear error message. The GUI additionally requires explicit user confirmation before sending a live config.

---

## Future: Multiple Sessions

The current architecture supports one active bot session at a time. The `session_id` field and `BotManager` abstraction are designed so that a future version can:

- Track multiple concurrent sessions (e.g., one per asset class)
- Return session lists from `/api/sessions`
- Target specific sessions for stop/restart

This is not implemented in v3.0.0 to avoid over-engineering, but the foundation is in place.
