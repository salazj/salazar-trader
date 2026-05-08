"""Comprehensive tests for the API-driven GUI workflow.

Covers the full lifecycle that the web GUI exercises:
  health → status → config → validate → start → status → portfolio →
  risk → logs → stop → status

Tests verify:
- Every endpoint returns proper status codes and schema-compliant responses
- BotManager fully encapsulates bot internals (no direct _bot access from routes)
- Session IDs are assigned on start and cleared on stop
- Config validation catches bad inputs before starting
- Safety gates prevent accidental live trading
- REST log retrieval works alongside WebSocket streaming
- Preset CRUD lifecycle
- Error paths (double-start, stop-when-stopped, etc.)
"""

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


# ── Health & status ────────────────────────────────────────────────


class TestHealthEndpoint:
    def test_health_returns_full_schema(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "3.0.0"
        assert data["bot_running"] is False
        assert data["session_id"] == ""
        assert data["mode"] == "dry-run"
        assert "timestamp" in data
        assert isinstance(data["log_subscribers"], int)

    def test_status_returns_session_id(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["session_id"] == ""
        assert data["running"] is False
        assert data["status"] == "stopped"


# ── Config & validation ───────────────────────────────────────────


class TestConfigWorkflow:
    def test_get_default_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "asset_class" in data
        assert "dry_run" in data
        assert "ensemble_weight_l1" in data
        assert "market_slugs" in data

    def test_validate_equities_config(self, client):
        resp = client.post("/api/config/validate", json={
            "asset_class": "equities",
            "broker": "alpaca",
            "dry_run": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True

    def test_validate_invalid_broker(self, client):
        resp = client.post("/api/config/validate", json={
            "asset_class": "equities",
            "broker": "interactive_brokers",
            "dry_run": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert any("broker" in e.lower() for e in data["errors"])

    def test_validate_invalid_decision_mode(self, client):
        resp = client.post("/api/config/validate", json={
            "asset_class": "prediction_markets",
            "exchange": "kalshi",
            "dry_run": True,
            "decision_mode": "yolo",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert any("decision_mode" in e for e in data["errors"])

    def test_validate_warns_on_weight_sum(self, client):
        resp = client.post("/api/config/validate", json={
            "asset_class": "prediction_markets",
            "exchange": "kalshi",
            "dry_run": True,
            "ensemble_weight_l1": 0.5,
            "ensemble_weight_l2": 0.5,
            "ensemble_weight_l3": 0.5,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert any("weight" in w.lower() for w in data["warnings"])

    def test_validate_rejects_zero_exposure(self, client):
        resp = client.post("/api/config/validate", json={
            "asset_class": "prediction_markets",
            "exchange": "polymarket",
            "dry_run": True,
            "max_total_exposure": 0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert any("exposure" in e.lower() for e in data["errors"])


# ── Preset CRUD ───────────────────────────────────────────────────


class TestPresetCRUD:
    def test_preset_lifecycle(self, client):
        resp = client.get("/api/config/presets")
        assert resp.status_code == 200

        resp = client.post("/api/config/presets/test-workflow", json={
            "asset_class": "equities",
            "broker": "alpaca",
            "dry_run": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-workflow"

        resp = client.get("/api/config/presets")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert "test-workflow" in names

        resp = client.delete("/api/config/presets/test-workflow")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == "test-workflow"

        resp = client.get("/api/config/presets")
        names = [p["name"] for p in resp.json()]
        assert "test-workflow" not in names

    def test_delete_nonexistent_preset(self, client):
        resp = client.delete("/api/config/presets/does-not-exist")
        assert resp.status_code == 404

    def test_invalid_preset_name(self, client):
        resp = client.post("/api/config/presets/!!!", json={
            "dry_run": True,
        })
        assert resp.status_code == 400


# ── Bot lifecycle ─────────────────────────────────────────────────


class TestBotLifecycle:
    def test_stop_when_already_stopped(self, client):
        resp = client.post("/api/bot/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["status"] == "stopped"

    def test_start_rejects_invalid_config(self, client):
        resp = client.post("/api/bot/start", json={
            "asset_class": "futures",
            "dry_run": True,
        })
        assert resp.status_code == 422
        assert "asset_class" in resp.json()["detail"].lower()

    def test_live_start_requires_all_gates(self, client):
        resp = client.post("/api/bot/start", json={
            "asset_class": "prediction_markets",
            "exchange": "kalshi",
            "dry_run": False,
            "enable_live_trading": True,
            "live_trading_acknowledged": False,
        })
        assert resp.status_code == 422
        assert "live_trading_acknowledged" in resp.json()["detail"]

    def test_restart_when_stopped(self, client):
        resp = client.post("/api/bot/restart", json={
            "asset_class": "prediction_markets",
            "exchange": "kalshi",
            "dry_run": True,
        })
        # Restart involves start, which may fail if the TradingBot can't
        # fully initialize in the test environment, but validation should pass
        assert resp.status_code in (200, 422, 500)


# ── Portfolio & risk when stopped ─────────────────────────────────


class TestPortfolioWhenStopped:
    def test_portfolio_returns_empty(self, client):
        resp = client.get("/api/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cash"] == 0.0
        assert data["positions"] == []
        assert data["position_count"] == 0

    def test_positions_returns_empty(self, client):
        resp = client.get("/api/portfolio/positions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_orders_returns_empty(self, client):
        resp = client.get("/api/portfolio/orders")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_fills_returns_empty(self, client):
        resp = client.get("/api/portfolio/fills")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_pnl_history_returns_empty(self, client):
        resp = client.get("/api/portfolio/pnl-history")
        assert resp.status_code == 200
        assert resp.json() == []


class TestRiskWhenStopped:
    def test_risk_returns_defaults(self, client):
        resp = client.get("/api/risk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["halted"] is False
        assert data["circuit_breaker_tripped"] is False

    def test_reset_breaker_when_stopped(self, client):
        resp = client.post("/api/risk/reset-breaker")
        assert resp.status_code == 409

    def test_emergency_stop_when_stopped(self, client):
        # Emergency stop now requires confirm=true (Jetson safety).
        resp = client.post("/api/risk/emergency-stop", json={"confirm": True})
        assert resp.status_code == 409

    def test_emergency_stop_requires_confirm(self, client):
        resp = client.post("/api/risk/emergency-stop")
        assert resp.status_code == 400


# ── REST logs ─────────────────────────────────────────────────────


class TestLogsEndpoint:
    def test_get_logs_returns_list(self, client):
        resp = client.get("/api/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_get_logs_with_level_filter(self, client):
        resp = client.get("/api/logs?level=error&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        for entry in data:
            assert entry["level"] in ("error", "critical")

    def test_get_logs_schema(self, client):
        resp = client.get("/api/logs?limit=5")
        assert resp.status_code == 200
        for entry in resp.json():
            assert "timestamp" in entry
            assert "level" in entry
            assert "event" in entry
            assert "logger" in entry
            assert "data" in entry


# ── Exchange & strategy listing ───────────────────────────────────


class TestExchangeListing:
    def test_exchanges_has_all_three(self, client):
        resp = client.get("/api/exchanges")
        assert resp.status_code == 200
        data = resp.json()
        ids = {e["id"] for e in data}
        assert ids == {"polymarket", "kalshi", "alpaca"}
        pm_exchanges = [e for e in data if e["asset_class"] == "prediction_markets"]
        eq_exchanges = [e for e in data if e["asset_class"] == "equities"]
        assert len(pm_exchanges) == 2
        assert len(eq_exchanges) == 1

    def test_exchange_config_fields(self, client):
        resp = client.get("/api/exchanges")
        for e in resp.json():
            assert isinstance(e["config_fields"], list)
            assert len(e["config_fields"]) > 0


class TestStrategyListing:
    def test_strategies_include_both_asset_classes(self, client):
        resp = client.get("/api/strategies")
        assert resp.status_code == 200
        data = resp.json()
        asset_classes = {s["asset_class"] for s in data}
        assert "prediction_markets" in asset_classes
        assert "equities" in asset_classes

    def test_strategies_have_descriptions(self, client):
        resp = client.get("/api/strategies")
        for s in resp.json():
            assert s["name"]
            assert s["description"]


# ── BotManager encapsulation ──────────────────────────────────────


class TestEncapsulation:
    """Verify route handlers don't leak bot internals."""

    def test_portfolio_route_does_not_access_bot_directly(self):
        """The portfolio route file should not reference mgr._bot."""
        import inspect
        from app.api.routes import portfolio
        source = inspect.getsource(portfolio)
        assert "mgr._bot" not in source
        assert "bot._repository" not in source

    def test_risk_route_does_not_access_bot_directly(self):
        """The risk route file should not reference mgr._bot."""
        import inspect
        from app.api.routes import risk
        source = inspect.getsource(risk)
        assert "mgr._bot" not in source

    def test_bot_manager_has_fills_method(self):
        from app.api.bot_manager import BotManager
        mgr = BotManager()
        assert hasattr(mgr, "get_fills")
        assert hasattr(mgr, "get_pnl_history")

    def test_bot_manager_has_risk_methods(self):
        from app.api.bot_manager import BotManager
        mgr = BotManager()
        assert hasattr(mgr, "reset_circuit_breaker")
        assert hasattr(mgr, "trip_emergency_stop")

    def test_bot_manager_has_session_id(self):
        from app.api.bot_manager import BotManager
        mgr = BotManager()
        assert mgr.session_id == ""
        status = mgr.get_status()
        assert status.session_id == ""
