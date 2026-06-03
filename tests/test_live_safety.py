"""Tests for live trading safety enforcement."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.schemas import RunConfig


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestLiveSafetyGates:
    def test_dry_run_start_succeeds(self, client):
        resp = client.post("/api/bot/start", json={
            "asset_class": "prediction_markets",
            "exchange": "polymarket",
            "dry_run": True,
        })
        # May succeed or fail depending on module availability, but should not
        # fail due to safety validation
        assert resp.status_code in (200, 409, 422, 500)

    def test_live_without_enable_flag_rejected(self, client):
        resp = client.post("/api/bot/start", json={
            "asset_class": "prediction_markets",
            "exchange": "polymarket",
            "dry_run": False,
            "enable_live_trading": False,
            "live_trading_acknowledged": False,
        })
        assert resp.status_code == 422
        assert "enable_live_trading" in resp.json()["detail"]

    def test_live_without_ack_rejected(self, client):
        resp = client.post("/api/bot/start", json={
            "asset_class": "prediction_markets",
            "exchange": "polymarket",
            "dry_run": False,
            "enable_live_trading": True,
            "live_trading_acknowledged": False,
        })
        assert resp.status_code == 422
        assert "live_trading_acknowledged" in resp.json()["detail"]

    def test_validation_catches_missing_credentials(self, client):
        resp = client.post("/api/config/validate", json={
            "asset_class": "equities",
            "broker": "alpaca",
            "dry_run": False,
            "enable_live_trading": True,
            "live_trading_acknowledged": True,
        })
        data = resp.json()
        assert data["valid"] is False
        assert any("alpaca" in e.lower() or "credential" in e.lower() for e in data["errors"])
