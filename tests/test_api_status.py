"""Tests for status and health API endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestStatusEndpoints:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_status_returns_stopped_by_default(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["status"] == "stopped"
        assert data["dry_run"] is True

    def test_exchanges_list(self, client):
        resp = client.get("/api/exchanges")
        assert resp.status_code == 200
        data = resp.json()
        ids = [e["id"] for e in data]
        assert "polymarket" in ids
        assert "kalshi" not in ids
        assert "alpaca" in ids

    def test_strategies_list(self, client):
        resp = client.get("/api/strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        names = [s["name"] for s in data]
        assert "stock_momentum" in names
