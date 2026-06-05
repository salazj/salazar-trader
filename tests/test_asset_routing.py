"""Tests for config-based routing between prediction markets and equities."""

from __future__ import annotations

from app.config.settings import Settings


class TestAssetRouting:
    def test_default_is_prediction_markets(self):
        s = Settings()
        assert s.asset_class == "prediction_markets"

    def test_equities_mode(self):
        s = Settings(asset_class="equities", broker="alpaca")
        assert s.asset_class == "equities"
        assert s.broker == "alpaca"

    def test_has_credentials_routes_by_asset_class(self):
        s = Settings(asset_class="equities")
        assert s.has_credentials is False

        s2 = Settings(
            asset_class="equities",
            alpaca_api_key="test-key",
            alpaca_secret_key="test-secret",
        )
        assert s2.has_credentials is True

    def test_has_credentials_polymarket(self):
        s = Settings(
            asset_class="prediction_markets",
            exchange="polymarket",
            private_key="0xabc",
            poly_api_key="key",
            poly_api_secret="secret",
        )
        assert s.has_credentials is True

    def test_is_live_requires_all_three_gates(self):
        s = Settings(
            dry_run=False,
            enable_live_trading=True,
            live_trading_acknowledged=True,
        )
        assert s.is_live is True

        s2 = Settings(
            dry_run=False,
            enable_live_trading=True,
            live_trading_acknowledged=False,
        )
        assert s2.is_live is False
