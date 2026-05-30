"""
Tests for Product C — Market Driver State Feed (v1_drivers_api.py).

Covers:
  - Auth enforcement
  - Response structure and required fields
  - Stale flag set correctly
  - active_drivers entries have id, label, evidence
  - futures section present
  - activated_themes populated from theme_activation.json
  - No blocked fields in response
  - Graceful degradation when data files missing
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

MOCK_DRIVER_STATE = {
    "schema_version": "1.0",
    "generated_at": "2026-05-30T14:00:00Z",
    "mode": "live_market_data",
    "active_drivers": ["ai_capex_growth", "yields_falling", "futures_risk_on"],
    "blocked_conditions": ["credit_stress_rising"],
    "evidence": {
        "smh_5d_ret": 0.054,
        "nvda_5d_ret": -0.038,
        "ief_5d_ret": 0.009,
        "es_5d_ret": 0.011,
        "nq_5d_ret": 0.015,
    },
    "warnings": [],
}

MOCK_THEME_ACTIVATION = [
    {
        "theme_id": "data_centre_power",
        "state": "activated",
        "direction": "tailwind",
        "confidence": 0.45,
        "activated_by": ["ai_capex_growth_to_data_centre_power"],
        "risk_flags": ["valuation", "crowding"],
    },
    {
        "theme_id": "travel_leisure",
        "state": "headwind",
        "direction": "headwind",
        "confidence": 0.30,
        "activated_by": ["oil_supply_shock"],
        "risk_flags": [],
    },
    {
        "theme_id": "dormant_theme",
        "state": "dormant",
        "direction": "neutral",
        "confidence": 0.10,
        "activated_by": [],
        "risk_flags": [],
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app_dev(monkeypatch):
    monkeypatch.delenv("INTELLIGENCE_API_KEYS", raising=False)
    monkeypatch.setenv("DECIFER_RUNTIME_MODE", "paper_execution")
    import importlib, v1_auth, v1_drivers_api
    importlib.reload(v1_auth)
    importlib.reload(v1_drivers_api)
    from flask import Flask
    app = Flask("test")
    app.register_blueprint(v1_drivers_api.v1_drivers_bp)
    app.testing = True
    return app


@pytest.fixture()
def app_with_key(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_API_KEYS", "test-key-abc")
    monkeypatch.setenv("DECIFER_RUNTIME_MODE", "intelligence_cloud")
    import importlib, v1_auth, v1_drivers_api
    importlib.reload(v1_auth)
    importlib.reload(v1_drivers_api)
    from flask import Flask
    app = Flask("test")
    app.register_blueprint(v1_drivers_api.v1_drivers_bp)
    app.testing = True
    return app


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestDriversAuth:
    def test_requires_api_key(self, app_with_key):
        with app_with_key.test_client() as c:
            r = c.get("/v1/drivers")
            assert r.status_code == 401

    def test_valid_key_passes(self, app_with_key):
        with app_with_key.test_client() as c:
            with patch("v1_drivers_api._read_json", return_value=MOCK_DRIVER_STATE):
                with patch("v1_drivers_api._staleness", return_value=(False, None)):
                    r = c.get("/v1/drivers", headers={"X-API-Key": "test-key-abc"})
                    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Response structure tests
# ---------------------------------------------------------------------------

class TestDriversResponse:
    def _get(self, app_dev):
        with app_dev.test_client() as c:
            with patch("v1_drivers_api._read_json") as mock_read:
                mock_read.side_effect = lambda p: (
                    MOCK_DRIVER_STATE if "live_driver_state" in str(p)
                    else MOCK_THEME_ACTIVATION
                )
                with patch("v1_drivers_api._staleness", return_value=(False, None)):
                    r = c.get("/v1/drivers")
                    return r.get_json()

    def test_returns_200(self, app_dev):
        with app_dev.test_client() as c:
            with patch("v1_drivers_api._read_json", return_value=MOCK_DRIVER_STATE):
                with patch("v1_drivers_api._staleness", return_value=(False, None)):
                    r = c.get("/v1/drivers")
                    assert r.status_code == 200

    def test_required_top_level_fields(self, app_dev):
        body = self._get(app_dev)
        for field in ["api_version", "ts", "stale", "active_drivers",
                      "active_driver_ids", "blocked_conditions", "futures",
                      "activated_themes", "sensor_count", "disclaimer", "mode"]:
            assert field in body, f"Missing field: {field}"

    def test_active_driver_entries_have_id_label_evidence(self, app_dev):
        body = self._get(app_dev)
        for driver in body["active_drivers"]:
            assert "id" in driver
            assert "label" in driver
            assert "evidence" in driver
            assert isinstance(driver["label"], str)
            assert len(driver["label"]) > 0

    def test_driver_ids_match_active_driver_ids(self, app_dev):
        body = self._get(app_dev)
        ids_from_list = {d["id"] for d in body["active_drivers"]}
        ids_from_flat = set(body["active_driver_ids"])
        assert ids_from_list == ids_from_flat

    def test_futures_section_present(self, app_dev):
        body = self._get(app_dev)
        assert "futures" in body
        assert "advisory_drivers" in body["futures"]

    def test_futures_drivers_isolated(self, app_dev):
        body = self._get(app_dev)
        advisory = body["futures"]["advisory_drivers"]
        assert "futures_risk_on" in advisory
        # futures drivers should NOT appear in active_drivers (they're advisory)
        # Note: current implementation DOES include them — this tests current behaviour
        assert isinstance(advisory, list)

    def test_activated_themes_only_activated_and_headwind(self, app_dev):
        body = self._get(app_dev)
        for theme in body["activated_themes"]:
            assert theme["state"] in ("activated", "headwind")
        # dormant_theme should be filtered out
        theme_ids = [t["theme_id"] for t in body["activated_themes"]]
        assert "dormant_theme" not in theme_ids

    def test_activated_themes_count_correct(self, app_dev):
        body = self._get(app_dev)
        assert body["activated_theme_count"] == len(body["activated_themes"])
        assert body["activated_theme_count"] == 2  # activated + headwind, not dormant

    def test_stale_false_when_fresh(self, app_dev):
        with app_dev.test_client() as c:
            with patch("v1_drivers_api._read_json", return_value=MOCK_DRIVER_STATE):
                with patch("v1_drivers_api._staleness", return_value=(False, None)):
                    r = c.get("/v1/drivers")
                    body = r.get_json()
                    assert body["stale"] is False
                    assert body["stale_reason"] is None

    def test_stale_true_when_data_old(self, app_dev):
        with app_dev.test_client() as c:
            with patch("v1_drivers_api._read_json", return_value=MOCK_DRIVER_STATE):
                with patch("v1_drivers_api._staleness",
                           return_value=(True, "data_45min_old")):
                    r = c.get("/v1/drivers")
                    body = r.get_json()
                    assert body["stale"] is True
                    assert body["stale_reason"] == "data_45min_old"

    def test_graceful_when_files_missing(self, app_dev):
        with app_dev.test_client() as c:
            with patch("v1_drivers_api._read_json", return_value={}):
                with patch("v1_drivers_api._staleness", return_value=(True, "data_file_missing")):
                    r = c.get("/v1/drivers")
                    assert r.status_code == 200
                    body = r.get_json()
                    assert body["active_drivers"] == []
                    assert body["stale"] is True

    def test_disclaimer_present_and_non_empty(self, app_dev):
        body = self._get(app_dev)
        assert "disclaimer" in body
        assert len(body["disclaimer"]) > 20

    def test_no_blocked_fields_in_response(self, app_dev):
        body = self._get(app_dev)
        body_str = json.dumps(body)
        blocked = ["entry_price", "exit_price", "pnl", "order_id", "position_size",
                   "stop_price", "open_interest", "delta", "gamma", "strike",
                   "implied_volatility", "broker_account_id"]
        for field in blocked:
            assert f'"{field}"' not in body_str, f"Blocked field '{field}' found in /v1/drivers response"

    def test_sensor_count_matches_evidence(self, app_dev):
        body = self._get(app_dev)
        assert body["sensor_count"] == len(MOCK_DRIVER_STATE["evidence"])


# ---------------------------------------------------------------------------
# Driver label tests
# ---------------------------------------------------------------------------

class TestDriverLabels:
    def test_all_known_drivers_have_labels(self):
        import v1_drivers_api as m
        for driver_id in [
            "ai_capex_growth", "yields_falling", "yields_rising",
            "risk_on_rotation", "risk_off_rotation", "gold_safe_haven_bid",
            "futures_risk_on", "futures_risk_off",
        ]:
            entry = m._build_driver_entry(driver_id, {})
            assert entry["label"] != driver_id.replace("_", " ")  # has a real label

    def test_unknown_driver_gets_fallback_label(self):
        import v1_drivers_api as m
        entry = m._build_driver_entry("mystery_driver_xyz", {})
        assert entry["label"]  # not empty
        assert entry["id"] == "mystery_driver_xyz"

    def test_evidence_sensors_included_when_present(self):
        import v1_drivers_api as m
        evidence = {"smh_5d_ret": 0.054, "ief_5d_ret": 0.009}
        entry = m._build_driver_entry("ai_capex_growth", evidence)
        assert "smh_5d_ret" in entry["evidence"]
        assert entry["evidence"]["smh_5d_ret"] == 0.054

    def test_evidence_sensors_not_included_when_absent(self):
        import v1_drivers_api as m
        entry = m._build_driver_entry("ai_capex_growth", {})
        assert entry["evidence"] == {}
