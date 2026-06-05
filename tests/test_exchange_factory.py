"""
Tests for exchange adapter factory and config-based exchange selection.
"""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.exchanges import build_exchange_adapter
from app.exchanges.base import Exchange


class TestExchangeFactory:
    def test_polymarket_selection(self, settings):
        settings.exchange = "polymarket"
        adapter = build_exchange_adapter(settings)
        assert adapter.exchange == Exchange.POLYMARKET

    def test_unknown_exchange_raises(self, settings):
        settings.exchange = "binance"
        with pytest.raises(ValueError, match="Unknown exchange"):
            build_exchange_adapter(settings)

    def test_case_insensitive(self, settings):
        settings.exchange = "Polymarket"
        adapter = build_exchange_adapter(settings)
        assert adapter.exchange == Exchange.POLYMARKET


class TestExchangeConfig:
    def test_default_exchange(self):
        s = Settings(
            dry_run=True,
            environment="test",
            log_level="WARNING",
            min_spread_threshold=0.01,
            max_spread_threshold=0.15,
        )
        assert s.exchange == "polymarket"

    def test_invalid_exchange(self):
        with pytest.raises(Exception):
            Settings(
                dry_run=True,
                environment="test",
                exchange="invalid",
                log_level="WARNING",
                min_spread_threshold=0.01,
                max_spread_threshold=0.15,
            )

    def test_polymarket_credentials_check(self):
        s = Settings(
            dry_run=True,
            environment="test",
            exchange="polymarket",
            private_key="0xabc",
            poly_api_key="test-key",
            poly_api_secret="test-secret",
            log_level="WARNING",
            min_spread_threshold=0.01,
            max_spread_threshold=0.15,
        )
        assert s.has_polymarket_credentials is True

    def test_polymarket_no_credentials(self):
        s = Settings(
            dry_run=True,
            environment="test",
            exchange="polymarket",
            log_level="WARNING",
            min_spread_threshold=0.01,
            max_spread_threshold=0.15,
        )
        assert s.has_polymarket_credentials is False

    def test_has_credentials_dispatch(self):
        s = Settings(
            dry_run=True,
            environment="test",
            exchange="polymarket",
            log_level="WARNING",
            min_spread_threshold=0.01,
            max_spread_threshold=0.15,
        )
        assert s.has_credentials is False

    def test_polymarket_secret_redaction(self):
        s = Settings(
            dry_run=True,
            environment="test",
            exchange="polymarket",
            private_key="super-secret",
            poly_api_secret="also-secret",
            log_level="WARNING",
            min_spread_threshold=0.01,
            max_spread_threshold=0.15,
        )
        repr_str = repr(s)
        assert "super-secret" not in repr_str
        assert "also-secret" not in repr_str
