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

## Configuration

Set the exchange in your `.env` file:

```bash
EXCHANGE=polymarket
```

Or pass it via CLI:

```bash
python -m app.main --exchange polymarket
```

### Polymarket Credentials

```bash
PRIVATE_KEY=your_wallet_private_key
POLY_API_KEY=your_api_key
POLY_API_SECRET=your_api_secret
POLY_PASSPHRASE=your_passphrase
```

## Architecture

```
app/exchanges/
├── __init__.py          # Factory: build_exchange_adapter(settings)
├── base.py              # Abstract base classes
└── polymarket/
    ├── adapter.py       # PolymarketAdapter (container)
    ├── market_data.py   # REST market data client
    ├── execution.py     # Order placement/cancellation
    └── websocket.py     # Real-time data feed
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

## Polymarket Conventions

### Prices

| Property        | Polymarket         |
|-----------------|--------------------|
| Unit            | Decimal (0.0–1.0)  |
| Conversion      | None (native)      |
| Valid range     | 0.0–1.0            |

### Identifiers

| Property        | Polymarket              |
|-----------------|-------------------------|
| Market ID       | `condition_id`          |
| Instrument ID   | `token_id`              |
| No-side token   | Separate `token_id`     |

### Authentication

| Property        | Polymarket                    |
|-----------------|-------------------------------|
| Method          | EVM wallet sign + API creds   |
| Headers         | `POLY-*`                      |
| Key type        | EVM private key               |

### Order Format

| Property        | Polymarket                    |
|-----------------|-------------------------------|
| Direction       | `side` = BUY/SELL             |
| Outcome         | Implicit in `token_id`        |
| Price field     | `price` (decimal)             |
| Size field      | `size` (float)                |

### WebSocket Channels

| Data Type       | Polymarket                    |
|-----------------|-------------------------------|
| Orderbook       | `price_change`                |
| Trades          | `trade`                       |
| User fills      | `trade` (user)                |

## Running in Dry-Run Mode (No Credentials Needed)

You can run the bot targeting Polymarket without credentials for observation-only mode:

```bash
EXCHANGE=polymarket
DRY_RUN=true
```

In dry-run mode:
- Market data is fetched from the public Polymarket API (no auth required)
- Orders are simulated locally — no orders reach Polymarket
- Balances and positions return zeroes
- All execution metrics are prefixed with `*_dry`

## Going Live on Polymarket

**All three safety gates must be explicitly enabled:**

```bash
EXCHANGE=polymarket
DRY_RUN=false
ENABLE_LIVE_TRADING=true
LIVE_TRADING_ACKNOWLEDGED=true

PRIVATE_KEY=your_wallet_private_key
POLY_API_KEY=your_api_key
POLY_API_SECRET=your_api_secret
POLY_PASSPHRASE=your_passphrase
```

The execution client performs a triple-check before every live order:
1. `DRY_RUN` is `false`
2. `ENABLE_LIVE_TRADING` is `true`
3. `LIVE_TRADING_ACKNOWLEDGED` is `true`

If any gate fails, the order is **rejected** and logged at CRITICAL level.

## Safety

- `DRY_RUN=true` is the default
- Three safety gates must be passed before any live order is submitted
- Exchange-specific credential validation happens at startup
- The decision engine, risk manager, and safety gates are exchange-independent
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
