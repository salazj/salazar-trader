"""Tests for config validation endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestConfigEndpoints:
    def test_get_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "asset_class" in data
        assert "dry_run" in data

    def test_validate_valid_config(self, client):
        resp = client.post("/api/config/validate", json={
            "asset_class": "prediction_markets",
            "exchange": "polymarket",
            "dry_run": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_validate_invalid_asset_class(self, client):
        resp = client.post("/api/config/validate", json={
            "asset_class": "futures",
            "exchange": "polymarket",
            "dry_run": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert any("asset_class" in e.lower() for e in data["errors"])

    def test_validate_live_without_gates(self, client):
        resp = client.post("/api/config/validate", json={
            "asset_class": "prediction_markets",
            "exchange": "polymarket",
            "dry_run": False,
            "enable_live_trading": False,
            "live_trading_acknowledged": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert any("enable_live_trading" in e for e in data["errors"])

    def test_presets_list_empty(self, client):
        resp = client.get("/api/config/presets")
        assert resp.status_code == 200
