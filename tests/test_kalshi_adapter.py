"""
Comprehensive tests for the Kalshi adapter layer:
  - Price conversion (cents ↔ decimal)
  - Market normalization
  - Orderbook normalization (snapshot + delta)
  - Trade normalization
  - Fill normalization
  - Order status mapping
  - Position normalization
  - Auth signature format
  - Execution client safety gates
  - WebSocket dispatch routing
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.models import Order, OrderStatus, Side
from app.exchanges.kalshi.normalizer import (
    KALSHI_STATUS_MAP,
    cents_to_decimal,
    decimal_to_cents,
    normalize_fill,
    normalize_market,
    normalize_order_status,
    normalize_order_update,
    normalize_orderbook,
    normalize_orderbook_delta,
    normalize_position,
    normalize_trade,
)


# ═══════════════════════════════════════════════════════════════════════
# Price conversion
# ═══════════════════════════════════════════════════════════════════════


class TestPriceConversion:
    def test_cents_to_decimal_zero(self):
        assert cents_to_decimal(0) == 0.0

    def test_cents_to_decimal_fifty(self):
        assert cents_to_decimal(50) == 0.5

    def test_cents_to_decimal_hundred(self):
        assert cents_to_decimal(100) == 1.0

    def test_cents_to_decimal_arbitrary(self):
        assert abs(cents_to_decimal(67) - 0.67) < 1e-9

    def test_cents_to_decimal_float_input(self):
        assert abs(cents_to_decimal(33.5) - 0.335) < 1e-9

    def test_decimal_to_cents_zero(self):
        assert decimal_to_cents(0.0) == 0

    def test_decimal_to_cents_half(self):
        assert decimal_to_cents(0.5) == 50

    def test_decimal_to_cents_one(self):
        assert decimal_to_cents(1.0) == 100

    def test_decimal_to_cents_rounding(self):
        assert decimal_to_cents(0.335) == 34

    def test_roundtrip(self):
        for cents in range(0, 101):
            assert decimal_to_cents(cents_to_decimal(cents)) == cents

    def test_edge_small_value(self):
        assert cents_to_decimal(1) == 0.01

    def test_edge_large_decimal(self):
        assert decimal_to_cents(0.99) == 99


# ═══════════════════════════════════════════════════════════════════════
# Market normalization
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeMarket:
    @pytest.fixture
    def raw_kalshi_market(self) -> dict:
        return {
            "ticker": "KXBTCD-26MAR14-B99750",
            "event_ticker": "KXBTCD-26MAR14",
            "title": "Will Bitcoin be above $99,750 on March 14?",
            "subtitle": "Bitcoin price contract",
            "status": "open",
            "yes_price": 65,
            "no_price": 35,
            "volume": 12345,
            "volume_24h": 500,
            "open_interest": 5000,
            "tick_size": 1,
            "min_order_size": 1,
            "close_time": "2026-03-14T23:59:00Z",
            "category": "Crypto",
            "result": None,
            "settlement_value": None,
        }

    def test_market_id(self, raw_kalshi_market):
        market = normalize_market(raw_kalshi_market)
        assert market.market_id == "KXBTCD-26MAR14-B99750"
        assert market.condition_id == "KXBTCD-26MAR14-B99750"

    def test_exchange_field(self, raw_kalshi_market):
        market = normalize_market(raw_kalshi_market)
        assert market.exchange == "kalshi"

    def test_question(self, raw_kalshi_market):
        market = normalize_market(raw_kalshi_market)
        assert "Bitcoin" in market.question

    def test_tokens(self, raw_kalshi_market):
        market = normalize_market(raw_kalshi_market)
        assert len(market.tokens) == 2
        yes_token = market.tokens[0]
        no_token = market.tokens[1]
        assert yes_token.outcome == "Yes"
        assert no_token.outcome == "No"
        assert yes_token.instrument_id == "KXBTCD-26MAR14-B99750"
        assert no_token.instrument_id == "KXBTCD-26MAR14-B99750-no"

    def test_active_status_open(self, raw_kalshi_market):
        market = normalize_market(raw_kalshi_market)
        assert market.active is True

    def test_active_status_trading(self, raw_kalshi_market):
        raw_kalshi_market["status"] = "trading"
        market = normalize_market(raw_kalshi_market)
        assert market.active is True

    def test_inactive_status(self, raw_kalshi_market):
        raw_kalshi_market["status"] = "closed"
        market = normalize_market(raw_kalshi_market)
        assert market.active is False

    def test_inactive_status_finalized(self, raw_kalshi_market):
        raw_kalshi_market["status"] = "finalized"
        market = normalize_market(raw_kalshi_market)
        assert market.active is False

    def test_exchange_data_populated(self, raw_kalshi_market):
        market = normalize_market(raw_kalshi_market)
        assert market.exchange_data["event_ticker"] == "KXBTCD-26MAR14"
        assert market.exchange_data["yes_price"] == 0.65
        assert market.exchange_data["no_price"] == pytest.approx(0.35, abs=1e-9)
        assert market.exchange_data["volume"] == 12345
        assert market.exchange_data["volume_24h"] == 500
        assert market.exchange_data["open_interest"] == 5000

    def test_tick_size_conversion(self, raw_kalshi_market):
        market = normalize_market(raw_kalshi_market)
        assert market.minimum_tick_size == 0.01

    def test_missing_yes_price_defaults_to_half(self):
        raw = {"ticker": "KXTEST", "status": "open"}
        market = normalize_market(raw)
        assert market.exchange_data["yes_price"] == 0.5

    def test_fallback_to_last_price(self):
        raw = {"ticker": "KXTEST", "last_price": 72, "status": "open"}
        market = normalize_market(raw)
        assert market.exchange_data["yes_price"] == 0.72

    def test_subtitle_used_when_no_title(self):
        raw = {"ticker": "KXTEST", "subtitle": "Some subtitle", "status": "open"}
        market = normalize_market(raw)
        assert market.question == "Some subtitle"

    def test_slug_lowercased(self, raw_kalshi_market):
        market = normalize_market(raw_kalshi_market)
        assert market.slug == market.slug.lower()


# ═══════════════════════════════════════════════════════════════════════
# Orderbook normalization
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeOrderbook:
    def test_basic_orderbook(self):
        raw = {
            "yes": [[65, 100], [64, 50], [63, 25]],
            "no": [[36, 80], [37, 40]],
        }
        book = normalize_orderbook("KXTEST", raw)
        assert book.market_id == "KXTEST"
        assert book.instrument_id == "KXTEST"
        assert book.exchange == "kalshi"
        assert len(book.bids) == 3
        assert len(book.asks) == 2

    def test_price_conversion(self):
        raw = {"yes": [[50, 100]], "no": [[51, 80]]}
        book = normalize_orderbook("KXTEST", raw)
        assert book.bids[0].price == 0.50
        assert book.asks[0].price == 0.51

    def test_bid_sorting_descending(self):
        raw = {"yes": [[40, 10], [60, 20], [50, 15]], "no": []}
        book = normalize_orderbook("KXTEST", raw)
        prices = [l.price for l in book.bids]
        assert prices == sorted(prices, reverse=True)

    def test_ask_sorting_ascending(self):
        raw = {"yes": [], "no": [[55, 10], [51, 20], [53, 15]]}
        book = normalize_orderbook("KXTEST", raw)
        prices = [l.price for l in book.asks]
        assert prices == sorted(prices)

    def test_empty_orderbook(self):
        raw = {"yes": [], "no": []}
        book = normalize_orderbook("KXTEST", raw)
        assert book.bids == []
        assert book.asks == []
        assert book.best_bid is None
        assert book.best_ask is None

    def test_dict_format_levels(self):
        raw = {
            "yes": [{"price": 60, "quantity": 100}],
            "no": [{"price": 41, "quantity": 50}],
        }
        book = normalize_orderbook("KXTEST", raw)
        assert len(book.bids) == 1
        assert book.bids[0].price == 0.60
        assert book.bids[0].size == 100.0
        assert len(book.asks) == 1
        assert book.asks[0].price == 0.41

    def test_fallback_bids_asks_keys(self):
        raw = {"bids": [[50, 10]], "asks": [[55, 20]]}
        book = normalize_orderbook("KXTEST", raw)
        assert len(book.bids) == 1
        assert len(book.asks) == 1


# ═══════════════════════════════════════════════════════════════════════
# Orderbook delta
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeOrderbookDelta:
    def test_delta_on_empty_book(self):
        delta = {"yes": [[55, 100]], "no": [[46, 50]]}
        book = normalize_orderbook_delta("KXTEST", delta)
        assert len(book.bids) == 1
        assert book.bids[0].price == 0.55
        assert len(book.asks) == 1

    def test_delta_removes_level(self):
        initial = normalize_orderbook("KXTEST", {"yes": [[50, 100], [49, 50]], "no": [[52, 30]]})
        delta = {"yes": [[50, 0]], "no": []}
        updated = normalize_orderbook_delta("KXTEST", delta, initial)
        assert len(updated.bids) == 1
        assert updated.bids[0].price == 0.49

    def test_delta_adds_new_level(self):
        initial = normalize_orderbook("KXTEST", {"yes": [[50, 100]], "no": []})
        delta = {"yes": [[48, 75]], "no": [[55, 40]]}
        updated = normalize_orderbook_delta("KXTEST", delta, initial)
        assert len(updated.bids) == 2
        assert len(updated.asks) == 1

    def test_delta_updates_existing_level(self):
        initial = normalize_orderbook("KXTEST", {"yes": [[50, 100]], "no": []})
        delta = {"yes": [[50, 200]], "no": []}
        updated = normalize_orderbook_delta("KXTEST", delta, initial)
        assert len(updated.bids) == 1
        assert updated.bids[0].size == 200.0

    def test_dict_format_delta(self):
        initial = normalize_orderbook("KXTEST", {"yes": [[50, 100]], "no": []})
        delta = {"yes": [{"price": 50, "delta": 0}], "no": []}
        updated = normalize_orderbook_delta("KXTEST", delta, initial)
        assert len(updated.bids) == 0


# ═══════════════════════════════════════════════════════════════════════
# Trade normalization
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeTrade:
    def test_basic_trade(self):
        raw = {
            "market_ticker": "KXBTC-123",
            "yes_price": 65,
            "count": 10,
            "taker_side": "yes",
            "created_time": "2026-03-11T15:00:00Z",
        }
        trade = normalize_trade(raw)
        assert trade.instrument_id == "KXBTC-123"
        assert trade.exchange == "kalshi"
        assert trade.price == 0.65
        assert trade.size == 10.0
        assert trade.side == Side.BUY

    def test_sell_side(self):
        raw = {"market_ticker": "KXTEST", "yes_price": 40, "count": 5, "taker_side": "no"}
        trade = normalize_trade(raw)
        assert trade.side == Side.SELL

    def test_buy_side_from_side_field(self):
        raw = {"ticker": "KXTEST", "price": 70, "quantity": 3, "side": "buy"}
        trade = normalize_trade(raw)
        assert trade.side == Side.BUY

    def test_timestamp_iso(self):
        raw = {"market_ticker": "KXTEST", "yes_price": 50, "count": 1, "created_time": "2026-01-15T10:30:00+00:00"}
        trade = normalize_trade(raw)
        assert trade.timestamp.year == 2026
        assert trade.timestamp.month == 1

    def test_timestamp_epoch_ms(self):
        raw = {"market_ticker": "KXTEST", "yes_price": 50, "count": 1, "ts": 1710100000000}
        trade = normalize_trade(raw)
        assert isinstance(trade.timestamp, datetime)

    def test_missing_timestamp_defaults_to_now(self):
        raw = {"market_ticker": "KXTEST", "yes_price": 50, "count": 1}
        trade = normalize_trade(raw)
        assert isinstance(trade.timestamp, datetime)
        assert (datetime.now(timezone.utc) - trade.timestamp).total_seconds() < 5

    def test_missing_fields_use_defaults(self):
        raw = {}
        trade = normalize_trade(raw)
        assert trade.instrument_id == ""
        assert trade.price == 0.5
        assert trade.size == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Fill normalization
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeFill:
    def test_basic_fill(self):
        raw = {
            "order_id": "abc-123",
            "ticker": "KXTEST",
            "yes_price": 55,
            "count": 5,
            "side": "yes",
            "action": "buy",
            "created_time": "2026-03-11T12:00:00Z",
        }
        fill = normalize_fill(raw)
        assert fill["order_id"] == "abc-123"
        assert fill["price"] == 0.55
        assert fill["size"] == 5.0
        assert fill["side"] == "yes"
        assert fill["action"] == "buy"
        assert fill["instrument_id"] == "KXTEST"

    def test_fill_with_market_ticker(self):
        raw = {"market_ticker": "KXOTHER", "price": 70, "quantity": 2, "side": "no", "action": "sell"}
        fill = normalize_fill(raw)
        assert fill["instrument_id"] == "KXOTHER"
        assert fill["price"] == 0.70

    def test_fill_missing_fields(self):
        raw = {}
        fill = normalize_fill(raw)
        assert fill["order_id"] == ""
        assert fill["price"] == 0.50
        assert fill["size"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Order status mapping
# ═══════════════════════════════════════════════════════════════════════


class TestOrderStatusMapping:
    def test_resting_maps_to_acknowledged(self):
        assert normalize_order_status("resting") == OrderStatus.ACKNOWLEDGED

    def test_pending(self):
        assert normalize_order_status("pending") == OrderStatus.PENDING

    def test_canceled(self):
        assert normalize_order_status("canceled") == OrderStatus.CANCELED

    def test_cancelled_british(self):
        assert normalize_order_status("cancelled") == OrderStatus.CANCELED

    def test_executed(self):
        assert normalize_order_status("executed") == OrderStatus.FILLED

    def test_partial(self):
        assert normalize_order_status("partial") == OrderStatus.PARTIALLY_FILLED

    def test_unknown_returns_none(self):
        assert normalize_order_status("unknown_state") is None

    def test_case_insensitive(self):
        assert normalize_order_status("RESTING") == OrderStatus.ACKNOWLEDGED
        assert normalize_order_status("Canceled") == OrderStatus.CANCELED


class TestNormalizeOrderUpdate:
    def test_basic_order_update(self):
        raw = {
            "order_id": "oid-1",
            "status": "resting",
            "count": 10,
            "remaining_count": 7,
            "ticker": "KXTEST",
        }
        result = normalize_order_update(raw)
        assert result["order_id"] == "oid-1"
        assert result["status"] == OrderStatus.ACKNOWLEDGED
        assert result["filled_size"] == 3
        assert result["remaining"] == 7
        assert result["ticker"] == "KXTEST"

    def test_fully_executed(self):
        raw = {"order_id": "oid-2", "status": "executed", "count": 10, "remaining_count": 0}
        result = normalize_order_update(raw)
        assert result["status"] == OrderStatus.FILLED
        assert result["filled_size"] == 10

    def test_unknown_status(self):
        raw = {"order_id": "oid-3", "status": "weird_state"}
        result = normalize_order_update(raw)
        assert result["status"] is None
        assert result["kalshi_status"] == "weird_state"


# ═══════════════════════════════════════════════════════════════════════
# Position normalization
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizePosition:
    def test_yes_position(self):
        raw = {
            "ticker": "KXPOS",
            "position": 15,
            "no_count": 0,
            "total_traded": 20,
            "market_exposure": 900,
            "realized_pnl": 50,
        }
        pos = normalize_position(raw)
        assert pos["instrument_id"] == "KXPOS"
        assert pos["size"] == 15.0
        assert pos["side"] == "yes"
        assert pos["realized_pnl"] == 0.50

    def test_no_position(self):
        raw = {"ticker": "KXPOS", "position": 0, "no_count": 10, "yes_count": 0, "total_traded": 10}
        pos = normalize_position(raw)
        assert pos["size"] == 10.0
        assert pos["side"] == "no"

    def test_empty_position(self):
        raw = {"ticker": "KXEMPTY"}
        pos = normalize_position(raw)
        assert pos["size"] == 0.0
        assert pos["avg_entry_price"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Auth signature
# ═══════════════════════════════════════════════════════════════════════


class TestKalshiAuth:
    @pytest.fixture
    def rsa_key_path(self, tmp_path) -> str:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        key_file = tmp_path / "test_kalshi.pem"
        key_file.write_bytes(pem)
        return str(key_file)

    def test_sign_request_returns_three_headers(self, rsa_key_path):
        from app.exchanges.kalshi.auth import KalshiAuth

        auth = KalshiAuth("test-api-key", rsa_key_path)
        headers = auth.sign_request("GET", "/trade-api/v2/markets")
        assert "KALSHI-ACCESS-KEY" in headers
        assert "KALSHI-ACCESS-TIMESTAMP" in headers
        assert "KALSHI-ACCESS-SIGNATURE" in headers

    def test_api_key_echoed(self, rsa_key_path):
        from app.exchanges.kalshi.auth import KalshiAuth

        auth = KalshiAuth("my-key-123", rsa_key_path)
        headers = auth.sign_request("POST", "/trade-api/v2/portfolio/orders")
        assert headers["KALSHI-ACCESS-KEY"] == "my-key-123"

    def test_timestamp_is_numeric_ms(self, rsa_key_path):
        from app.exchanges.kalshi.auth import KalshiAuth

        auth = KalshiAuth("key", rsa_key_path)
        headers = auth.sign_request("GET", "/path")
        ts = int(headers["KALSHI-ACCESS-TIMESTAMP"])
        assert ts > 1_000_000_000_000

    def test_signature_is_base64(self, rsa_key_path):
        from app.exchanges.kalshi.auth import KalshiAuth

        auth = KalshiAuth("key", rsa_key_path)
        headers = auth.sign_request("GET", "/path")
        sig = headers["KALSHI-ACCESS-SIGNATURE"]
        decoded = base64.b64decode(sig)
        assert len(decoded) > 0

    def test_different_methods_produce_different_signatures(self, rsa_key_path):
        from app.exchanges.kalshi.auth import KalshiAuth

        auth = KalshiAuth("key", rsa_key_path)
        h1 = auth.sign_request("GET", "/path")
        h2 = auth.sign_request("POST", "/path")
        assert h1["KALSHI-ACCESS-SIGNATURE"] != h2["KALSHI-ACCESS-SIGNATURE"]

    def test_missing_key_file_raises(self, tmp_path):
        from app.exchanges.kalshi.auth import KalshiAuth

        with pytest.raises(FileNotFoundError):
            KalshiAuth("key", str(tmp_path / "nonexistent.pem"))

    def test_empty_api_key_raises(self, rsa_key_path):
        from app.exchanges.kalshi.auth import KalshiAuth

        with pytest.raises(ValueError, match="must not be empty"):
            KalshiAuth("", rsa_key_path)

    def test_non_rsa_key_raises(self, tmp_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        ec_key = ec.generate_private_key(ec.SECP256R1())
        pem = ec_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        key_file = tmp_path / "ec_key.pem"
        key_file.write_bytes(pem)

        from app.exchanges.kalshi.auth import KalshiAuth

        with pytest.raises(TypeError, match="RSA"):
            KalshiAuth("key", str(key_file))


class TestKalshiMarketDataAuth:
    def test_get_markets_signs_path_without_query_params(self, settings):
        """Kalshi requires signing the path WITHOUT query parameters."""
        from app.exchanges.kalshi.market_data import KalshiMarketDataClient

        client = KalshiMarketDataClient(settings)
        client._auth = MagicMock()
        client._auth.sign_request.return_value = {"X-Test": "signed"}

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"markets": [], "cursor": ""}
        client._client.get = AsyncMock(return_value=response)

        asyncio.get_event_loop().run_until_complete(client.get_markets())

        client._auth.sign_request.assert_called_once()
        _, signed_path = client._auth.sign_request.call_args[0]
        assert signed_path == "/trade-api/v2/markets"
        assert "?" not in signed_path

        asyncio.get_event_loop().run_until_complete(client.close())

    def test_default_base_url_uses_elections_domain(self, settings):
        """Production URL must use api.elections.kalshi.com (not trading-api)."""
        assert "api.elections.kalshi.com" in settings.kalshi_base_url


# ═══════════════════════════════════════════════════════════════════════
# Execution client
# ═══════════════════════════════════════════════════════════════════════


class TestKalshiExecution:
    @pytest.fixture
    def kalshi_settings(self, settings):
        settings.exchange = "kalshi"
        return settings

    def test_dry_run_order_placement(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)
        assert client.is_dry_run is True

        order = Order(
            order_id="test-1",
            market_id="KXTEST",
            token_id="KXTEST",
            instrument_id="KXTEST",
            exchange="kalshi",
            side=Side.BUY,
            price=0.65,
            size=10,
        )

        result = asyncio.get_event_loop().run_until_complete(client.place_order(order))
        assert result.status == OrderStatus.ACKNOWLEDGED
        assert result.exchange_order_id.startswith("DRY-KALSHI-")

    def test_dry_run_cancel(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)
        order = Order(
            order_id="test-cancel",
            market_id="KXTEST",
            instrument_id="KXTEST",
            exchange="kalshi",
            side=Side.BUY,
            price=0.50,
            size=5,
            status=OrderStatus.ACKNOWLEDGED,
            exchange_order_id="DRY-KALSHI-abc",
        )
        result = asyncio.get_event_loop().run_until_complete(client.cancel_order(order))
        assert result.status == OrderStatus.CANCELED

    def test_dry_run_cancel_all(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)
        asyncio.get_event_loop().run_until_complete(client.cancel_all())

    def test_dry_run_balance_returns_zero(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)
        balance = asyncio.get_event_loop().run_until_complete(client.get_balance())
        assert balance == 0.0

    def test_dry_run_positions_returns_empty(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)
        positions = asyncio.get_event_loop().run_until_complete(client.get_open_positions())
        assert positions == []

    def test_dry_run_open_orders_returns_empty(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)
        orders = asyncio.get_event_loop().run_until_complete(client.get_open_orders())
        assert orders == []

    def test_build_order_body_yes_buy(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)
        order = Order(
            order_id="ob-1",
            market_id="KXTEST",
            instrument_id="KXTEST",
            exchange="kalshi",
            side=Side.BUY,
            price=0.65,
            size=10,
        )
        body = client._build_order_body(order)
        assert body["ticker"] == "KXTEST"
        assert body["action"] == "buy"
        assert body["side"] == "yes"
        assert body["yes_price"] == 65
        assert body["count"] == 10

    def test_build_order_body_no_buy(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)
        order = Order(
            order_id="ob-2",
            market_id="KXTEST",
            instrument_id="KXTEST-no",
            exchange="kalshi",
            side=Side.BUY,
            price=0.40,
            size=5,
        )
        body = client._build_order_body(order)
        assert body["ticker"] == "KXTEST"
        assert body["action"] == "buy"
        assert body["side"] == "no"
        assert body["no_price"] == 60
        assert body["count"] == 5

    def test_build_order_body_yes_sell(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)
        order = Order(
            order_id="ob-3",
            market_id="KXTEST",
            instrument_id="KXTEST",
            exchange="kalshi",
            side=Side.SELL,
            price=0.70,
            size=3,
        )
        body = client._build_order_body(order)
        assert body["action"] == "sell"
        assert body["side"] == "yes"
        assert body["yes_price"] == 70

    def test_build_order_body_price_clamped(self, kalshi_settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        client = KalshiExecutionClient(kalshi_settings)

        order_high = Order(
            order_id="ob-4", market_id="KX", instrument_id="KX",
            exchange="kalshi", side=Side.BUY, price=1.0, size=1,
        )
        body_high = client._build_order_body(order_high)
        assert body_high["yes_price"] == 99

        order_low = Order(
            order_id="ob-5", market_id="KX", instrument_id="KX",
            exchange="kalshi", side=Side.BUY, price=0.0, size=1,
        )
        body_low = client._build_order_body(order_low)
        assert body_low["yes_price"] == 1

    def test_live_place_blocked_without_ack(self, settings):
        from app.exchanges.kalshi.execution import KalshiExecutionClient

        settings.exchange = "kalshi"
        settings.dry_run = False
        settings.enable_live_trading = True
        settings.live_trading_acknowledged = False

        client = KalshiExecutionClient.__new__(KalshiExecutionClient)
        client._settings = settings
        client._dry_run = False
        client._auth = None
        client._client = None

        order = Order(
            order_id="blocked-1", market_id="KX", instrument_id="KX",
            exchange="kalshi", side=Side.BUY, price=0.5, size=1,
        )
        result = asyncio.get_event_loop().run_until_complete(client._live_place(order))
        assert result.status == OrderStatus.REJECTED


# ═══════════════════════════════════════════════════════════════════════
# WebSocket dispatch
# ═══════════════════════════════════════════════════════════════════════


class TestKalshiWebSocketDispatch:
    @pytest.fixture
    def ws_client(self, settings):
        settings.exchange = "kalshi"
        from app.exchanges.kalshi.websocket import KalshiWebSocketClient

        return KalshiWebSocketClient(settings)

    def test_subscribe_book(self, ws_client):
        ws_client.subscribe_book(["KXTEST-1", "KXTEST-2"])
        assert len(ws_client._subscriptions) == 1
        assert ws_client._subscriptions[0]["params"]["channels"] == ["orderbook_delta"]
        assert ws_client._subscriptions[0]["params"]["market_tickers"] == ["KXTEST-1", "KXTEST-2"]

    def test_subscribe_trades(self, ws_client):
        ws_client.subscribe_trades(["KXTEST"])
        assert len(ws_client._subscriptions) == 1
        assert ws_client._subscriptions[0]["params"]["channels"] == ["trade"]

    def test_subscribe_user_without_auth(self, ws_client):
        ws_client._auth = None
        ws_client.subscribe_user()
        assert len(ws_client._subscriptions) == 0

    def test_dispatch_orderbook_snapshot(self, ws_client):
        received = []

        async def handler(msg):
            received.append(msg)

        ws_client.on("book", handler)

        msg = {
            "type": "orderbook_snapshot",
            "channel": "orderbook_snapshot",
            "msg": {
                "market_ticker": "KXTEST",
                "yes": [[55, 100], [54, 50]],
                "no": [[46, 80]],
            },
        }

        asyncio.get_event_loop().run_until_complete(ws_client._dispatch(msg))
        assert len(received) == 1
        assert received[0]["type"] == "snapshot"
        assert received[0]["exchange"] == "kalshi"
        assert len(received[0]["assets"]) == 1
        assert len(received[0]["assets"][0]["bids"]) == 2

    def test_dispatch_trade(self, ws_client):
        received = []

        async def handler(msg):
            received.append(msg)

        ws_client.on("trade", handler)

        msg = {
            "type": "trade",
            "channel": "trade",
            "msg": {
                "trades": [
                    {"market_ticker": "KXTEST", "yes_price": 60, "count": 5, "taker_side": "yes"}
                ]
            },
        }

        asyncio.get_event_loop().run_until_complete(ws_client._dispatch(msg))
        assert len(received) == 1
        assert received[0]["type"] == "trade"
        assert received[0]["trades"][0]["price"] == 0.60

    def test_dispatch_fill(self, ws_client):
        received = []

        async def handler(msg):
            received.append(msg)

        ws_client.on("user", handler)

        msg = {
            "type": "fill",
            "channel": "fill",
            "msg": {
                "fills": [
                    {"order_id": "f1", "ticker": "KXTEST", "yes_price": 65, "count": 3, "side": "yes", "action": "buy"}
                ]
            },
        }

        asyncio.get_event_loop().run_until_complete(ws_client._dispatch(msg))
        assert len(received) == 1
        assert received[0]["event"] == "fill"
        assert received[0]["price"] == 0.65

    def test_dispatch_order_update(self, ws_client):
        received = []

        async def handler(msg):
            received.append(msg)

        ws_client.on("user", handler)

        msg = {
            "type": "order_group_updates",
            "channel": "order_group_updates",
            "msg": {
                "orders": [
                    {"order_id": "o1", "status": "resting", "count": 10, "remaining_count": 10, "ticker": "KXTEST"}
                ]
            },
        }

        asyncio.get_event_loop().run_until_complete(ws_client._dispatch(msg))
        assert len(received) == 1
        assert received[0]["event"] == "order_update"
        assert received[0]["status"] == OrderStatus.ACKNOWLEDGED

    def test_dispatch_ignores_subscribed_control(self, ws_client):
        received = []

        async def handler(msg):
            received.append(msg)

        ws_client.on("book", handler)
        ws_client.on("trade", handler)
        ws_client.on("user", handler)

        msg = {"type": "subscribed", "sid": 1}
        asyncio.get_event_loop().run_until_complete(ws_client._dispatch(msg))
        assert len(received) == 0

    def test_stale_detection(self, ws_client):
        assert ws_client.is_stale is True  # no messages received yet
        ws_client._last_message_time = datetime.now(timezone.utc)
        assert ws_client.is_stale is False

    def test_is_connected_default_false(self, ws_client):
        assert ws_client.is_connected is False

    def test_book_state_maintained(self, ws_client):
        """Verify that orderbook state is accumulated across deltas."""
        received = []

        async def handler(msg):
            received.append(msg)

        ws_client.on("book", handler)

        snapshot_msg = {
            "type": "orderbook_snapshot",
            "channel": "orderbook_snapshot",
            "msg": {"market_ticker": "KXTEST", "yes": [[50, 100]], "no": [[55, 80]]},
        }
        asyncio.get_event_loop().run_until_complete(ws_client._dispatch(snapshot_msg))
        assert "KXTEST" in ws_client._book_snapshots

        delta_msg = {
            "type": "orderbook_delta",
            "channel": "orderbook_delta",
            "msg": {"market_ticker": "KXTEST", "yes": [[48, 50]], "no": []},
        }
        asyncio.get_event_loop().run_until_complete(ws_client._dispatch(delta_msg))
        book = ws_client._book_snapshots["KXTEST"]
        assert len(book.bids) == 2
