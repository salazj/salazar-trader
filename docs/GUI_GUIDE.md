# Web GUI Guide

The $alazar-Trader web GUI provides a full browser-based interface for controlling, monitoring, and configuring the trading bot. **All interaction goes through the backend control API** — the GUI never touches bot internals directly.

## Accessing the GUI

After running `docker compose up --build`, open your browser to:

- **Local**: `http://localhost:3000`
- **Remote VM**: `http://<your-vm-ip>:3000`

The GUI works on desktop and mobile browsers. A green dot in the sidebar indicates the WebSocket connection to the backend is healthy.

## Pages

### Dashboard (`/`)

The main overview page shows:
- **Bot status**: running/stopped with session ID, dry-run vs. live, current exchange/broker
- **Daily P&L**: real-time profit and loss with color coding
- **Exposure**: total position value, position count, and cash
- **Risk state**: normal or halted, with a daily loss progress bar
- **Open positions table**: side badges, entry/mark prices, unrealized P&L
- **Recent orders**: latest order activity with status badges
- **Quick controls**: Stop button (when running) or "Configure & Start" link (when stopped)

Errors from the bot appear as a red banner at the top. The Dashboard uses toast notifications instead of browser alerts.

### Run Configuration (`/config`)

Configure and launch a trading session:

1. **Select mode**: Two visually distinct cards for **Polymarket** and **Stocks**. Each card has a unique color and icon with a checkmark on the selected card.

2. **Exchange/broker settings**: Depends on the selected mode:
   - Polymarket: exchange dropdown, max tracked/subscribed markets, include/exclude categories, market slugs
   - Stocks: broker dropdown, universe mode (manual/filtered), tickers, sector filter, price/volume filters, extended hours toggle

3. **Strategies**: Checkboxes filtered by asset class with descriptions. Custom checkbox styling with animated check marks.

4. **Intelligence & NLP**: Configure NLP provider (mock/newsapi/polygon) and LLM provider (none/openai). API keys are set via `.env`, not the GUI.

5. **Decision Engine**: Mode selector (conservative/balanced/aggressive) with color coding. Slider controls for L1/L2/L3 weights and confidence threshold. Numeric inputs for minimum layers and evidence signals.

6. **Risk Limits**: Asset-class-specific risk limits. Stocks show dollar-based limits; prediction markets show per-market and total exposure limits.

7. **Trading Mode**: Dry Run (green) or Live (red). Live mode reveals safety gate toggles (styled as toggle switches) with explicit warnings.

8. **Presets**: Save and load configuration presets. Open the preset drawer, name a configuration, and save. Load a saved preset to populate all fields.

9. **Validate & Start**: Validates config through the API before starting. Shows validation errors (red) and warnings (amber) inline. The start button label changes based on mode ("Start Dry Run" vs. "⚠ Start Live Trading").

### Live Logs (`/logs`)

Real-time log viewer:
- Streams logs via WebSocket with connection status indicator
- Filter by severity (DEBUG/INFO/WARNING/ERROR)
- Search across event text, logger, and data fields
- Auto-scroll with explicit play/pause toggle buttons
- Color-coded by severity level
- Compact monospace layout with timestamps and level badges
- Export logs as JSON
- Clear button to reset the log buffer

Log history is also available via `GET /api/logs` on page load.

### Portfolio (`/portfolio`)

Four tabs with icons:
- **Positions**: open positions with entry price, mark price, unrealized P&L, % return, and side badges
- **Orders**: recent orders with fill size and status badges
- **Fills**: executed fills with P&L
- **P&L History**: area chart with gradient fill showing daily P&L over time

All data comes from the backend API and updates via WebSocket.

### Risk Controls (`/risk`)

- System status with halt badge and reason
- Circuit breaker status with reset button
- Daily loss tracking with visual progress bar (green → amber → red)
- Consecutive losses, orders/minute, emergency stop file status
- Emergency stop with double-confirmation dialog
- Live trading warning banner when active
- All actions use toast notifications for feedback

## Live Trading Safety

The GUI enforces the same three safety gates as the backend:
1. `DRY_RUN=false`
2. `ENABLE_LIVE_TRADING=true`
3. `LIVE_TRADING_ACKNOWLEDGED=true`

When live trading is enabled:
- A **red warning banner** with pulsing icons appears at the top of every page
- The config page shows explicit toggle switches for each gate
- The backend validates credentials before starting
- The start button turns red and changes label to "⚠ Start Live Trading"

The GUI will **never** silently enable live trading. All live-trading controls require explicit user interaction.

## API Communication

The GUI communicates exclusively through the control API:

| Data | Protocol | Endpoint |
|------|----------|----------|
| Bot status | WebSocket | `/ws/status` (every 2s) |
| Portfolio | WebSocket | `/ws/portfolio` (every 5s) |
| Live logs | WebSocket | `/ws/logs?level=info` |
| Log history | REST | `GET /api/logs` |
| Config | REST | `GET/POST /api/config` |
| Validation | REST | `POST /api/config/validate` |
| Start/Stop | REST | `POST /api/bot/start|stop|restart` |
| Risk actions | REST | `POST /api/risk/reset-breaker|emergency-stop` |

See `docs/API_REFERENCE.md` for full endpoint documentation.

## Connection Status

A green pulsing dot in the sidebar header indicates the WebSocket connection is healthy. A red dot indicates the connection is lost — the GUI will automatically reconnect every 3 seconds.

The Logs page also shows an explicit "Streaming" / "Disconnected" indicator.
