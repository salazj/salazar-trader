"""
Kalshi data normalizer — converts Kalshi API responses to internal models.

Key conversions:
  - Prices:  Kalshi cents (0-100) → internal decimal (0.0-1.0)
  - IDs:     Kalshi `ticker` → internal `instrument_id`
  - Groups:  Kalshi `event_ticker` → stored in `exchange_data`
  - Sides:   Kalshi `yes`/`no` + `buy`/`sell` → internal Side + OutcomeSide
  - Orders:  Kalshi order JSON → internal OrderStatus enum
  - Fills:   Kalshi fill JSON → internal fill dict
  - Book:    Kalshi [[price, size], …] → list[PriceLevel]

Kalshi represents the "No" outcome as 100-yes_price.  We model each market
as a single YES instrument (the ticker itself).  When the user wants to
trade the NO side, the adapter flips action/price accordingly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.data.models import (
    Market,
    MarketToken,
    OrderbookSnapshot,
    OrderStatus,
    PriceLevel,
    Side,
    Trade,
)
from app.utils.helpers import utc_now


# ── Price helpers ─────────────────────────────────────────────────────


def cents_to_decimal(cents: int | float) -> float:
    """Convert Kalshi cents (0-100) to decimal probability (0.0-1.0)."""
    return float(cents) / 100.0


def decimal_to_cents(decimal_price: float) -> int:
    """Convert decimal probability (0.0-1.0) to Kalshi cents (0-100)."""
    return int(round(decimal_price * 100))


# ── Market normalizer ────────────────────────────────────────────────


def _parse_dollar_price(raw: dict[str, Any], *keys: str) -> float | None:
    """Extract the first non-zero dollar price from the given field names."""
    for key in keys:
        val = raw.get(key)
        if val is not None:
            fval = float(val)
            if fval > 0:
                return fval
    return None


def normalize_market(raw: dict[str, Any]) -> Market:
    """Parse a Kalshi market API response into a normalized Market."""
    ticker = raw.get("ticker", "")
    event_ticker = raw.get("event_ticker", "")
    subtitle = raw.get("subtitle", "")
    title = raw.get("title", raw.get("yes_sub_title", ""))

    uses_dollars = raw.get("response_price_units") == "usd_cent" or "yes_bid_dollars" in raw

    if uses_dollars:
        yes_price = _parse_dollar_price(
            raw, "yes_bid_dollars", "last_price_dollars", "previous_price_dollars",
        )
        yes_ask = _parse_dollar_price(raw, "yes_ask_dollars")
        volume = float(raw.get("volume_fp") or 0)
        volume_24h = float(raw.get("volume_24h_fp") or 0)
        open_interest = float(raw.get("open_interest_fp") or 0)
    else:
        yes_price_raw = raw.get("yes_bid") or raw.get("yes_price") or raw.get("last_price")
        yes_price = cents_to_decimal(yes_price_raw) if yes_price_raw else None
        yes_ask_raw = raw.get("yes_ask")
        yes_ask = cents_to_decimal(yes_ask_raw) if yes_ask_raw else None
        volume = float(raw.get("volume") or 0)
        volume_24h = float(raw.get("volume_24h") or 0)
        open_interest = float(raw.get("open_interest") or 0)

    no_price = (1.0 - yes_price) if yes_price is not None else None

    tokens = [
        MarketToken(token_id=ticker, instrument_id=ticker, outcome="Yes"),
        MarketToken(token_id=f"{ticker}-no", instrument_id=f"{ticker}-no", outcome="No"),
    ]

    status = raw.get("status", "")
    active = status in ("open", "active", "trading")

    tick_size_raw = raw.get("tick_size", 1)
    min_tick = cents_to_decimal(tick_size_raw) if tick_size_raw else 0.01

    close_time = raw.get("close_time") or raw.get("expiration_time")

    return Market(
        condition_id=ticker,
        market_id=ticker,
        question=title or subtitle or ticker,
        slug=ticker.lower(),
        tokens=tokens,
        end_date=close_time,
        active=active,
        minimum_order_size=float(raw.get("min_order_size", 1)),
        minimum_tick_size=min_tick,
        exchange="kalshi",
        exchange_data={
            "event_ticker": event_ticker,
            "subtitle": subtitle,
            "yes_price": yes_price,
            "yes_ask": yes_ask,
            "no_price": no_price,
            "volume": volume,
            "volume_24h": volume_24h,
            "open_interest": open_interest,
            "status": status,
            "result": raw.get("result"),
            "category": raw.get("category"),
            "settlement_value": raw.get("settlement_value"),
            "can_close_early": raw.get("can_close_early"),
            "expiration_value": raw.get("expiration_value"),
        },
    )


# ── Orderbook normalizer ─────────────────────────────────────────────


def _parse_book_levels(raw_levels: list, descending: bool, *, is_dollars: bool = False) -> list[PriceLevel]:
    """Parse [[price, size], …] into sorted PriceLevels."""
    levels: list[PriceLevel] = []
    for lvl in raw_levels:
        if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            price = float(lvl[0])
            size = float(lvl[1])
            if not is_dollars:
                price = cents_to_decimal(price)
            levels.append(PriceLevel(price=price, size=size))
        elif isinstance(lvl, dict):
            price = float(lvl.get("price", lvl.get("yes_price", 0)))
            size = float(lvl.get("quantity", lvl.get("size", lvl.get("count", 0))))
            if not is_dollars:
                price = cents_to_decimal(price)
            levels.append(PriceLevel(price=price, size=size))
    levels.sort(key=lambda l: l.price, reverse=descending)
    return levels


def normalize_orderbook(ticker: str, raw: dict[str, Any]) -> OrderbookSnapshot:
    """Parse a Kalshi orderbook response into a normalized OrderbookSnapshot.

    Kalshi API v2 uses ``{"yes_dollars": [...], "no_dollars": [...]}`` (dollar format)
    or legacy ``{"yes": [...], "no": [...]}`` (cents format).

    NO side prices are converted to YES perspective: ask_price = 1.0 - no_price.
    """
    is_dollars = "yes_dollars" in raw or "no_dollars" in raw
    yes_key = "yes_dollars" if is_dollars else "yes"
    no_key = "no_dollars" if is_dollars else "no"

    bids = _parse_book_levels(raw.get(yes_key, raw.get("bids", [])), descending=True, is_dollars=is_dollars)
    raw_no_levels = _parse_book_levels(raw.get(no_key, raw.get("asks", [])), descending=False, is_dollars=is_dollars)
    asks = [PriceLevel(price=1.0 - lvl.price, size=lvl.size) for lvl in raw_no_levels if lvl.price < 1.0]
    asks.sort(key=lambda l: l.price)

    return OrderbookSnapshot(
        market_id=ticker,
        instrument_id=ticker,
        token_id=ticker,
        exchange="kalshi",
        bids=bids,
        asks=asks,
    )


def normalize_orderbook_delta(
    ticker: str,
    delta: dict[str, Any],
    current_book: OrderbookSnapshot | None = None,
) -> OrderbookSnapshot:
    """Apply an incremental orderbook_delta message on top of an existing book.

    If no existing book is provided, treat the delta as a full snapshot.
    Delta format: ``{"price": cents, "delta": signed_qty, "side": "yes"|"no"}``
    or a batch: ``{"market_ticker": str, "yes": [...], "no": [...]}``.
    """
    if current_book is None:
        current_book = OrderbookSnapshot(
            market_id=ticker, instrument_id=ticker, token_id=ticker, exchange="kalshi"
        )

    bid_map: dict[float, float] = {l.price: l.size for l in current_book.bids}
    ask_map: dict[float, float] = {l.price: l.size for l in current_book.asks}

    yes_deltas = delta.get("yes", [])
    no_deltas = delta.get("no", [])

    for d in yes_deltas:
        if isinstance(d, (list, tuple)) and len(d) >= 2:
            price = cents_to_decimal(d[0])
            qty = float(d[1])
        elif isinstance(d, dict):
            price = cents_to_decimal(d.get("price", 0))
            qty = float(d.get("delta", d.get("quantity", 0)))
        else:
            continue
        if qty <= 0:
            bid_map.pop(price, None)
        else:
            bid_map[price] = qty

    for d in no_deltas:
        if isinstance(d, (list, tuple)) and len(d) >= 2:
            price = cents_to_decimal(d[0])
            qty = float(d[1])
        elif isinstance(d, dict):
            price = cents_to_decimal(d.get("price", 0))
            qty = float(d.get("delta", d.get("quantity", 0)))
        else:
            continue
        if qty <= 0:
            ask_map.pop(price, None)
        else:
            ask_map[price] = qty

    bids = sorted(
        [PriceLevel(price=p, size=s) for p, s in bid_map.items()],
        key=lambda l: l.price,
        reverse=True,
    )
    asks = sorted(
        [PriceLevel(price=p, size=s) for p, s in ask_map.items()],
        key=lambda l: l.price,
    )

    return OrderbookSnapshot(
        market_id=ticker,
        instrument_id=ticker,
        token_id=ticker,
        exchange="kalshi",
        bids=bids,
        asks=asks,
    )


# ── Trade normalizer ─────────────────────────────────────────────────


def normalize_trade(raw: dict[str, Any]) -> Trade:
    """Parse a Kalshi trade payload into a normalized Trade."""
    ticker = raw.get("market_ticker", raw.get("ticker", ""))

    price_dollars = raw.get("yes_price_dollars")
    if price_dollars is not None:
        price_raw = float(price_dollars) * 100
    else:
        price_raw = raw.get("yes_price", raw.get("price", 50))

    count_raw = raw.get("count_fp", raw.get("count", raw.get("size", raw.get("quantity", 0))))
    count = float(count_raw) if count_raw else 0

    taker_side = raw.get("taker_side", raw.get("side", "")).lower()
    if taker_side in ("yes", "buy"):
        side = Side.BUY
    else:
        side = Side.SELL

    ts_raw = raw.get("created_time", raw.get("ts", raw.get("timestamp")))
    if isinstance(ts_raw, str):
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = utc_now()
    elif isinstance(ts_raw, (int, float)):
        ts = datetime.fromtimestamp(ts_raw / 1000 if ts_raw > 1e12 else ts_raw, tz=timezone.utc)
    else:
        ts = utc_now()

    return Trade(
        market_id=ticker,
        token_id=ticker,
        instrument_id=ticker,
        exchange="kalshi",
        price=cents_to_decimal(price_raw),
        size=float(count),
        side=side,
        timestamp=ts,
    )


# ── Fill normalizer ──────────────────────────────────────────────────


def normalize_fill(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse a Kalshi fill payload into a dict consumable by main.py's fill handler.

    Returns keys: order_id, price, size, side, ticker, timestamp.
    """
    order_id = raw.get("order_id", "")
    ticker = raw.get("ticker", raw.get("market_ticker", ""))
    yes_price = raw.get("yes_price", raw.get("price", 50))
    count = raw.get("count", raw.get("size", raw.get("quantity", 0)))
    side_str = raw.get("side", "yes").lower()
    action_str = raw.get("action", "buy").lower()

    return {
        "order_id": order_id,
        "price": cents_to_decimal(yes_price),
        "size": float(count),
        "side": side_str,
        "action": action_str,
        "ticker": ticker,
        "instrument_id": ticker,
        "timestamp": raw.get("created_time", raw.get("ts")),
    }


# ── Order status mapping ─────────────────────────────────────────────

KALSHI_STATUS_MAP: dict[str, OrderStatus] = {
    "resting": OrderStatus.ACKNOWLEDGED,
    "pending": OrderStatus.PENDING,
    "canceled": OrderStatus.CANCELED,
    "cancelled": OrderStatus.CANCELED,
    "executed": OrderStatus.FILLED,
    "partial": OrderStatus.PARTIALLY_FILLED,
}


def normalize_order_status(kalshi_status: str) -> OrderStatus | None:
    """Map a Kalshi order status string to the internal OrderStatus enum."""
    return KALSHI_STATUS_MAP.get(kalshi_status.lower())


def normalize_order_update(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse a Kalshi order update (REST or WS) into a dict for the execution engine.

    Returns keys: order_id, status, filled_size, ticker, remaining.
    """
    order_id = raw.get("order_id", "")
    kalshi_status = raw.get("status", "")
    status = normalize_order_status(kalshi_status)
    remaining = int(raw.get("remaining_count", raw.get("remaining", 0)))
    count = int(raw.get("count", raw.get("original_count", raw.get("size", 0))))
    filled = count - remaining if count > 0 else 0

    return {
        "order_id": order_id,
        "status": status,
        "filled_size": filled,
        "ticker": raw.get("ticker", raw.get("market_ticker", "")),
        "remaining": remaining,
        "kalshi_status": kalshi_status,
    }


# ── Position normalizer ──────────────────────────────────────────────


def normalize_position(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse a Kalshi portfolio position into a dict for the portfolio tracker.

    Kalshi positions include ``market_exposure`` (in cents), ``total_traded``,
    and running realized PnL.
    """
    ticker = raw.get("ticker", raw.get("market_ticker", ""))
    yes_count = int(raw.get("position", raw.get("yes_count", 0)))
    no_count = int(raw.get("no_count", 0))
    realized_pnl_cents = raw.get("realized_pnl", 0)
    market_exposure_cents = raw.get("market_exposure", 0)

    size = yes_count if yes_count else no_count
    side = "yes" if yes_count >= no_count else "no"

    total_traded = int(raw.get("total_traded", 0))
    if total_traded > 0 and size > 0 and market_exposure_cents:
        avg_entry = cents_to_decimal(abs(market_exposure_cents) / size)
    else:
        avg_entry = 0.0

    return {
        "instrument_id": ticker,
        "token_id": ticker,
        "size": float(abs(size)),
        "side": side,
        "avg_entry_price": avg_entry,
        "realized_pnl": cents_to_decimal(realized_pnl_cents),
        "total_traded": total_traded,
        "market_exposure": cents_to_decimal(market_exposure_cents),
    }
