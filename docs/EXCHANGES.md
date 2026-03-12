# Exchanges and Brokers

## Overview

The trading platform uses a pluggable adapter architecture for both prediction-market
exchanges and stock brokers. The core system (strategies, decision engine, risk manager,
portfolio tracker) is asset-agnostic and operates on normalized data models.

- Prediction market adapters live in `app/exchanges/`
- Stock broker adapters live in `app/brokers/`

## Supported Exchanges and Brokers

### Prediction Markets

| Exchange    | Status | Config Value  | Auth Method           |
|-------------|--------|---------------|-----------------------|
| Polymarket  | Full   | `polymarket`  | EVM wallet + API key  |
| Kalshi      | Full   | `kalshi`      | RSA-PSS signature     |

## Configuration

Set the exchange in your `.env` file:

```bash
EXCHANGE=polymarket   # or "kalshi"
```

Or pass it via CLI:

```bash
python -m app.main --exchange kalshi
```

### Polymarket Credentials

```bash
PRIVATE_KEY=your_wallet_private_key
POLY_API_KEY=your_api_key
POLY_API_SECRET=your_api_secret
POLY_PASSPHRASE=your_passphrase
```

### Kalshi Credentials

```bash
KALSHI_API_KEY=your_api_key
KALSHI_PRIVATE_KEY_PATH=/path/to/your/kalshi_private.pem
KALSHI_DEMO_MODE=true      # Use demo environment (recommended first)
KALSHI_BASE_URL=https://api.elections.kalshi.com/trade-api/v2   # production (default)
KALSHI_WS_URL=wss://api.elections.kalshi.com/trade-api/ws/v2    # production (default)
```

#### Generating Kalshi API Credentials

1. Create an account at [kalshi.com](https://kalshi.com)
2. Go to **Account → API Keys** (<https://kalshi.com/account/api-keys>)
3. Click **Create API Key** — save the key ID (this is your `KALSHI_API_KEY`)
4. Generate an RSA key pair locally:

```bash
openssl genrsa -out kalshi_private.pem 4096
openssl rsa -in kalshi_private.pem -pubout -out kalshi_public.pem
```

5. Upload `kalshi_public.pem` to Kalshi
6. Store `kalshi_private.pem` securely and set `KALSHI_PRIVATE_KEY_PATH` to its absolute path
7. **Never commit or share your private key**

## Architecture

```
app/exchanges/
├── __init__.py          # Factory: build_exchange_adapter(settings)
├── base.py              # Abstract base classes
├── polymarket/
│   ├── adapter.py       # PolymarketAdapter (container)
│   ├── market_data.py   # REST market data client
│   ├── execution.py     # Order placement/cancellation
│   └── websocket.py     # Real-time data feed
└── kalshi/
    ├── adapter.py       # KalshiAdapter (container)
    ├── auth.py          # RSA-PSS signature generation
    ├── market_data.py   # REST market data client (+ events, search, trade history)
    ├── execution.py     # Order placement/cancellation/fills/balances
    ├── normalizer.py    # Price/data conversion (full normalizer suite)
    └── websocket.py     # Real-time data feed with per-ticker book state
```

### Base Interfaces

All exchange adapters implement these abstract base classes:

- **`BaseExchangeAdapter`** — Container wiring together sub-clients
- **`BaseMarketDataClient`** — `get_markets()`, `get_market()`, `get_orderbook()`, `get_midpoint()`
- **`BaseExecutionClient`** — `place_order()`, `cancel_order()`, `cancel_all()`, `get_balance()`, `get_open_positions()`
- **`BaseWebSocketClient`** — `subscribe_book()`, `subscribe_trades()`, `subscribe_user()`, `connect()`, `disconnect()`

### Normalized Data Models

Exchange-specific data is normalized into these shared models (in `app/data/models.py`):

| Model              | Key Fields                                      |
|--------------------|-------------------------------------------------|
| `Market`           | `market_id`, `exchange`, `tokens`, `exchange_data` |
| `MarketToken`      | `instrument_id`, `token_id`, `outcome`           |
| `OrderbookSnapshot`| `instrument_id`, `exchange`, `bids`, `asks`      |
| `Trade`            | `instrument_id`, `exchange`, `price`, `side`     |
| `Order`            | `instrument_id`, `exchange`, `side`, `price`     |
| `Signal`           | `instrument_id`, `exchange`, `action`            |
| `Position`         | `instrument_id`, `exchange`, `token_side`        |

Both `token_id` (legacy Polymarket name) and `instrument_id` (exchange-agnostic name)
are kept in sync via model constructors, so existing code continues to work.

## Key Differences Between Exchanges

### Prices

| Property        | Polymarket         | Kalshi                  |
|-----------------|--------------------|-------------------------|
| Unit            | Decimal (0.0–1.0)  | Cents (0–100)           |
| Conversion      | None (native)      | `cents_to_decimal()`    |
| Valid range      | 0.0–1.0            | 1–99 cents for orders   |

### Identifiers

| Property        | Polymarket              | Kalshi                    |
|-----------------|-------------------------|---------------------------|
| Market ID       | `condition_id`          | `ticker`                  |
| Instrument ID   | `token_id`              | `ticker`                  |
| Group key       | —                       | `event_ticker`            |
| No-side token   | Separate `token_id`     | `{ticker}-no` (synthetic) |

### Authentication

| Property        | Polymarket                    | Kalshi                          |
|-----------------|-------------------------------|---------------------------------|
| Method          | EVM wallet sign + API creds   | RSA-PSS signature (SHA-256)     |
| Headers         | `POLY-*`                      | `KALSHI-ACCESS-*`               |
| Message format  | —                             | `{timestamp_ms}{METHOD}{path}`  |
| Key type        | EVM private key               | RSA 4096-bit PEM                |

### Order Format

| Property        | Polymarket                    | Kalshi                            |
|-----------------|-------------------------------|-----------------------------------|
| Direction       | `side` = BUY/SELL             | `action` = buy/sell               |
| Outcome         | Implicit in `token_id`        | `side` = yes/no                   |
| Price field     | `price` (decimal)             | `yes_price` or `no_price` (cents) |
| Size field      | `size` (float)                | `count` (integer)                 |

### WebSocket Channels

| Data Type       | Polymarket                    | Kalshi                            |
|-----------------|-------------------------------|-----------------------------------|
| Orderbook       | `price_change`                | `orderbook_delta` / `orderbook_snapshot` |
| Trades          | `trade`                       | `trade`                           |
| User fills      | `trade` (user)                | `fill`                            |
| Order updates   | —                             | `order_group_updates`             |
| Price ticks     | —                             | `ticker`                          |

## Kalshi Normalizer

The normalizer (`app/exchanges/kalshi/normalizer.py`) handles all Kalshi → internal conversions:

| Function                     | Description                                      |
|------------------------------|--------------------------------------------------|
| `cents_to_decimal()`         | 0–100 cents → 0.0–1.0 decimal                   |
| `decimal_to_cents()`         | 0.0–1.0 decimal → 0–100 cents (rounded)         |
| `normalize_market()`         | Kalshi market JSON → `Market` model              |
| `normalize_orderbook()`      | Kalshi book JSON → `OrderbookSnapshot`           |
| `normalize_orderbook_delta()`| Apply incremental deltas to existing book state  |
| `normalize_trade()`          | Kalshi trade JSON → `Trade` model                |
| `normalize_fill()`           | Kalshi fill JSON → fill dict                     |
| `normalize_order_status()`   | Kalshi status string → `OrderStatus` enum        |
| `normalize_order_update()`   | Kalshi order JSON → order update dict            |
| `normalize_position()`       | Kalshi position JSON → position dict             |

### Order Status Mapping

| Kalshi Status  | Internal Status          |
|---------------|--------------------------|
| `resting`     | `ACKNOWLEDGED`           |
| `pending`     | `PENDING`                |
| `canceled`    | `CANCELED`               |
| `cancelled`   | `CANCELED`               |
| `executed`    | `FILLED`                 |
| `partial`     | `PARTIALLY_FILLED`       |

## Kalshi Demo Environment

Kalshi provides a full demo/sandbox environment for testing:

```bash
KALSHI_DEMO_MODE=true
```

This routes all API calls to `https://demo-api.kalshi.co/trade-api/v2` and
WebSocket connections to `wss://demo-api.kalshi.co/trade-api/ws/v2`.
No real money is at risk in demo mode.

To use demo mode:

1. Create a demo account at [demo.kalshi.co](https://demo.kalshi.co)
2. Generate API keys on the demo portal
3. Generate an RSA key pair and upload the public key to demo
4. Set your `.env`:

```bash
EXCHANGE=kalshi
KALSHI_API_KEY=your_demo_api_key
KALSHI_PRIVATE_KEY_PATH=/path/to/kalshi_private.pem
KALSHI_DEMO_MODE=true
DRY_RUN=true
```

5. Run:

```bash
# Docker
docker compose up bot

# Local
python -m app.main --exchange kalshi
```

## Running Kalshi in Dry-Run Mode (No Credentials Needed)

You can run the bot targeting Kalshi without credentials for observation-only mode:

```bash
EXCHANGE=kalshi
DRY_RUN=true
```

In dry-run mode:
- Market data is fetched from the public Kalshi API (no auth required)
- Orders are simulated locally — no orders reach Kalshi
- Balances and positions return zeroes
- All execution metrics are prefixed with `*_dry`

## Going Live on Kalshi

**All three safety gates must be explicitly enabled:**

```bash
EXCHANGE=kalshi
DRY_RUN=false
ENABLE_LIVE_TRADING=true
LIVE_TRADING_ACKNOWLEDGED=true

KALSHI_API_KEY=your_production_api_key
KALSHI_PRIVATE_KEY_PATH=/path/to/kalshi_private.pem
KALSHI_DEMO_MODE=false
```

The execution client performs a triple-check before every live order:
1. `DRY_RUN` is `false`
2. `ENABLE_LIVE_TRADING` is `true`
3. `LIVE_TRADING_ACKNOWLEDGED` is `true`

If any gate fails, the order is **rejected** and logged at CRITICAL level.

## Safety

- `DRY_RUN=true` is the default for both exchanges
- Three safety gates must be passed before any live order is submitted
- Exchange-specific credential validation happens at startup
- The decision engine, risk manager, and safety gates are exchange-independent
- Order prices are clamped to 1–99 cents to avoid invalid Kalshi orders
- Balance and position queries fail gracefully and return safe defaults

## Adding a New Exchange

1. Create `app/exchanges/newexchange/` with:
   - `adapter.py` — implement `BaseExchangeAdapter`
   - `market_data.py` — implement `BaseMarketDataClient`
   - `execution.py` — implement `BaseExecutionClient`
   - `websocket.py` — implement `BaseWebSocketClient`
   - `normalizer.py` — convert exchange data to normalized models
   - `auth.py` — exchange-specific authentication

2. Register in `app/exchanges/__init__.py`:
   ```python
   elif exchange == "newexchange":
       return NewExchangeAdapter(settings)
   ```

3. Add config fields to `app/config/settings.py`

4. Add `"newexchange"` to the `validate_exchange` validator

5. Update `.env.example` with credential fields

6. Add tests in `tests/test_newexchange_adapter.py`
