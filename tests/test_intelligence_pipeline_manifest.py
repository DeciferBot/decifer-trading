"""
test_intelligence_pipeline_manifest.py

Tests for run_intelligence_pipeline._derive_regime_from_drivers and
_write_manifest market_regime field.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from run_intelligence_pipeline import _derive_regime_from_drivers, _write_manifest


# ---------------------------------------------------------------------------
# _derive_regime_from_drivers
# ---------------------------------------------------------------------------

class TestDeriveRegimeFromDrivers:

    def test_risk_on_drivers_produce_trending_up(self):
        drivers = ["ai_capex_growth", "small_cap_risk_on", "futures_risk_on"]
        assert _derive_regime_from_drivers(drivers) == "TRENDING_UP"

    def test_risk_off_alone_produces_trending_down(self):
        assert _derive_regime_from_drivers(["futures_risk_off"]) == "TRENDING_DOWN"
        assert _derive_regime_from_drivers(["yields_rising"]) == "TRENDING_DOWN"

    def test_mixed_on_and_off_produces_trending_up(self):
        # When both risk-on and risk-off drivers are present, risk-on wins
        # (on_count > 0 check fires before the pure risk-off check).
        drivers = ["yields_rising", "ai_capex_growth", "small_cap_risk_on"]
        assert _derive_regime_from_drivers(drivers) == "TRENDING_UP"

    def test_no_drivers_produces_range_bound(self):
        assert _derive_regime_from_drivers([]) == "RANGE_BOUND"

    def test_sector_catalysts_do_not_produce_trending_down(self):
        """geopolitical_risk_rising and oil_supply_shock are sector catalysts —
        they must not push the regime to TRENDING_DOWN."""
        drivers = ["geopolitical_risk_rising", "oil_supply_shock"]
        assert _derive_regime_from_drivers(drivers) == "RANGE_BOUND"

    def test_live_driver_set_produces_trending_up(self):
        """Exact driver set that was producing spurious 'mixed' in production."""
        drivers = [
            "ai_capex_growth", "ai_compute_demand", "yields_falling",
            "oil_supply_shock", "geopolitical_risk_rising",
            "small_cap_risk_on", "futures_risk_on",
        ]
        assert _derive_regime_from_drivers(drivers) == "TRENDING_UP"

    def test_yields_falling_contributes_to_risk_on(self):
        assert _derive_regime_from_drivers(["yields_falling"]) == "TRENDING_UP"

    def test_ai_compute_demand_alone_is_trending_up(self):
        assert _derive_regime_from_drivers(["ai_compute_demand"]) == "TRENDING_UP"


# ---------------------------------------------------------------------------
# _write_manifest — market_regime written to disk
# ---------------------------------------------------------------------------

class TestWriteManifestMarketRegime:

    def test_market_regime_written_when_provided(self, tmp_path, monkeypatch):
        import run_intelligence_pipeline as rip
        monkeypatch.setattr(rip, "_MANIFEST_PATH", str(tmp_path / "current_manifest.json"))
        monkeypatch.setattr(rip, "_LIVE_DIR", str(tmp_path))

        _write_manifest("data/live/active_opportunity_universe.json", 10,
                        market_regime="TRENDING_UP")

        data = json.loads((tmp_path / "current_manifest.json").read_text())
        assert data["market_regime"] == "TRENDING_UP"

    def test_market_regime_none_when_not_provided(self, tmp_path, monkeypatch):
        import run_intelligence_pipeline as rip
        monkeypatch.setattr(rip, "_MANIFEST_PATH", str(tmp_path / "current_manifest.json"))
        monkeypatch.setattr(rip, "_LIVE_DIR", str(tmp_path))

        _write_manifest("data/live/active_opportunity_universe.json", 5)

        data = json.loads((tmp_path / "current_manifest.json").read_text())
        assert data["market_regime"] is None

    def test_market_regime_trending_down(self, tmp_path, monkeypatch):
        import run_intelligence_pipeline as rip
        monkeypatch.setattr(rip, "_MANIFEST_PATH", str(tmp_path / "current_manifest.json"))
        monkeypatch.setattr(rip, "_LIVE_DIR", str(tmp_path))

        _write_manifest("data/live/active_opportunity_universe.json", 3,
                        market_regime="TRENDING_DOWN")

        data = json.loads((tmp_path / "current_manifest.json").read_text())
        assert data["market_regime"] == "TRENDING_DOWN"

    def test_handoff_enabled_is_still_true(self, tmp_path, monkeypatch):
        import run_intelligence_pipeline as rip
        monkeypatch.setattr(rip, "_MANIFEST_PATH", str(tmp_path / "current_manifest.json"))
        monkeypatch.setattr(rip, "_LIVE_DIR", str(tmp_path))

        _write_manifest("data/live/active_opportunity_universe.json", 7,
                        market_regime="TRENDING_UP")

        data = json.loads((tmp_path / "current_manifest.json").read_text())
        assert data["handoff_enabled"] is True
        assert data["no_executable_trade_instructions"] is True
