"""
test_intelligence_execution_separation.py

Tests for the Intelligence/Execution separation foundation (Sprint: v4.37.0).

Covers:
  1. runtime_config — mode logic, is_execution_enabled(), assert_execution_allowed()
  2. architecture/layer_boundary — classification and boundary helpers
  3. saas_intelligence_output — allowed/blocked field validation
  4. market_now_builder — payload structure and customer-safe validation
  5. Execution guards — execute_buy/sell/short block in intelligence_cloud mode
  6. Import boundary — verifier logic for intelligence/saas modules
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Repo root on sys.path ─────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# =============================================================================
# 1. runtime_config
# =============================================================================

class TestRuntimeConfig:
    """Tests for runtime_config mode logic."""

    def _reload_with_env(self, env: dict[str, str]):
        """Force-reload runtime_config with a controlled environment."""
        for key in list(os.environ):
            if key.startswith("DECIFER_"):
                del os.environ[key]
        os.environ.update(env)
        if "runtime_config" in sys.modules:
            del sys.modules["runtime_config"]
        import runtime_config
        importlib.reload(runtime_config)
        return runtime_config

    def teardown_method(self, _method):
        for key in list(os.environ):
            if key.startswith("DECIFER_"):
                del os.environ[key]
        if "runtime_config" in sys.modules:
            del sys.modules["runtime_config"]

    # ── intelligence_cloud always blocks ──────────────────────────────────────

    def test_intelligence_cloud_blocks_execution(self):
        rc = self._reload_with_env({
            "DECIFER_RUNTIME_MODE": "intelligence_cloud",
            "DECIFER_EXECUTION_ENABLED": "true",  # should be overridden
        })
        assert rc.is_intelligence_cloud_mode() is True
        assert rc.is_execution_enabled() is False
        with pytest.raises(rc.ExecutionBlockedError):
            rc.assert_execution_allowed("execute_buy")

    def test_intelligence_cloud_is_execution_enabled_false_regardless(self):
        rc = self._reload_with_env({
            "DECIFER_RUNTIME_MODE": "intelligence_cloud",
            "DECIFER_EXECUTION_ENABLED": "true",
        })
        assert rc.is_execution_enabled() is False

    # ── local_dev does not allow execution by default ─────────────────────────

    def test_local_dev_default_does_not_enable_execution(self):
        rc = self._reload_with_env({"DECIFER_RUNTIME_MODE": "local_dev"})
        assert rc.is_execution_enabled() is False
        with pytest.raises(rc.ExecutionBlockedError):
            rc.assert_execution_allowed("execute_sell")

    def test_local_dev_explicit_enabled_still_blocked(self):
        """local_dev + DECIFER_EXECUTION_ENABLED=true is still not an execution mode."""
        rc = self._reload_with_env({
            "DECIFER_RUNTIME_MODE": "local_dev",
            "DECIFER_EXECUTION_ENABLED": "true",
        })
        assert rc.is_execution_enabled() is False

    # ── paper_execution requires explicit enablement ──────────────────────────

    def test_paper_execution_without_enabled_flag_blocks(self):
        rc = self._reload_with_env({
            "DECIFER_RUNTIME_MODE": "paper_execution",
            "DECIFER_EXECUTION_ENABLED": "false",
        })
        assert rc.is_execution_enabled() is False
        with pytest.raises(rc.ExecutionBlockedError):
            rc.assert_execution_allowed("execute_buy")

    def test_paper_execution_with_enabled_flag_allows(self):
        rc = self._reload_with_env({
            "DECIFER_RUNTIME_MODE": "paper_execution",
            "DECIFER_EXECUTION_ENABLED": "true",
        })
        assert rc.is_execution_enabled() is True
        rc.assert_execution_allowed("execute_buy")  # must not raise

    # ── full_trading requires explicit enablement ─────────────────────────────

    def test_full_trading_without_enabled_flag_blocks(self):
        rc = self._reload_with_env({
            "DECIFER_RUNTIME_MODE": "full_trading",
            "DECIFER_EXECUTION_ENABLED": "false",
        })
        assert rc.is_execution_enabled() is False

    def test_full_trading_with_enabled_flag_allows(self):
        rc = self._reload_with_env({
            "DECIFER_RUNTIME_MODE": "full_trading",
            "DECIFER_EXECUTION_ENABLED": "true",
        })
        assert rc.is_execution_enabled() is True
        rc.assert_execution_allowed("flatten_all")  # must not raise

    # ── invalid mode raises ───────────────────────────────────────────────────

    def test_invalid_runtime_mode_raises(self):
        for key in list(os.environ):
            if key.startswith("DECIFER_"):
                del os.environ[key]
        os.environ["DECIFER_RUNTIME_MODE"] = "not_a_real_mode"
        if "runtime_config" in sys.modules:
            del sys.modules["runtime_config"]
        with pytest.raises(ValueError, match="not_a_real_mode"):
            import runtime_config  # noqa: F401

    # ── mode defaults ────────────────────────────────────────────────────────

    def test_default_mode_is_local_dev(self):
        rc = self._reload_with_env({})
        assert rc.runtime_mode == "local_dev"

    def test_default_execution_enabled_is_false(self):
        rc = self._reload_with_env({})
        assert rc.execution_enabled is False

    def test_default_mobile_read_only_is_true(self):
        rc = self._reload_with_env({})
        assert rc.mobile_read_only is True


# =============================================================================
# 2. architecture/layer_boundary
# =============================================================================

class TestLayerBoundary:
    """Tests for layer boundary classification."""

    def setup_method(self, _method):
        from architecture.layer_boundary import Layer, classify_module_path, is_execution_path, is_customer_safe_path
        self.Layer = Layer
        self.classify = classify_module_path
        self.is_exec = is_execution_path
        self.is_safe = is_customer_safe_path

    def test_execution_modules_classified_correctly(self):
        for stem in ("orders_core", "orders_options", "orders_portfolio", "bot_ibkr"):
            path = _REPO_ROOT / f"{stem}.py"
            assert self.classify(path) == self.Layer.EXECUTION, f"{stem} should be EXECUTION"

    def test_intelligence_modules_classified_correctly(self):
        for stem in ("market_intelligence", "live_driver_resolver", "scanner", "candidate_resolver"):
            path = _REPO_ROOT / f"{stem}.py"
            assert self.classify(path) == self.Layer.INTELLIGENCE, f"{stem} should be INTELLIGENCE"

    def test_saas_output_modules_classified_correctly(self):
        for stem in ("mobile_api", "saas_intelligence_output", "market_now_builder"):
            path = _REPO_ROOT / f"{stem}.py"
            assert self.classify(path) == self.Layer.SAAS_OUTPUT, f"{stem} should be SAAS_OUTPUT"

    def test_test_files_classified_as_test_only(self):
        p = _REPO_ROOT / "tests" / "test_foo.py"
        assert self.classify(p) == self.Layer.TEST_ONLY

    def test_conftest_classified_as_test_only(self):
        p = _REPO_ROOT / "tests" / "conftest.py"
        assert self.classify(p) == self.Layer.TEST_ONLY

    def test_is_execution_path(self):
        assert self.is_exec(_REPO_ROOT / "orders_core.py") is True
        assert self.is_exec(_REPO_ROOT / "market_intelligence.py") is False

    def test_is_customer_safe_path(self):
        assert self.is_safe(_REPO_ROOT / "mobile_api.py") is True
        assert self.is_safe(_REPO_ROOT / "saas_intelligence_output.py") is True
        assert self.is_safe(_REPO_ROOT / "orders_core.py") is False
        assert self.is_safe(_REPO_ROOT / "bot_trading.py") is False

    def test_runtime_config_is_shared_library(self):
        from architecture.layer_boundary import Layer, classify_module_path
        p = _REPO_ROOT / "runtime_config.py"
        assert classify_module_path(p) == Layer.SHARED_LIBRARY

    def test_signals_subpackage_is_intelligence(self):
        from architecture.layer_boundary import Layer, classify_module_path
        p = _REPO_ROOT / "signals" / "__init__.py"
        assert classify_module_path(p) == Layer.INTELLIGENCE


# =============================================================================
# 3. saas_intelligence_output
# =============================================================================

class TestSaaSIntelligenceOutput:
    """Tests for customer-safe payload validation."""

    def setup_method(self, _method):
        from saas_intelligence_output import (
            SaaSIntelligencePayload,
            SaaSPayloadValidationError,
            validate_customer_payload,
            get_allowed_fields,
            get_blocked_fields,
        )
        self.Payload = SaaSIntelligencePayload
        self.Error = SaaSPayloadValidationError
        self.validate = validate_customer_payload
        self.allowed = get_allowed_fields()
        self.blocked = get_blocked_fields()

    def test_valid_payload_passes_validation(self):
        p = self.Payload(
            market_regime_label="Trending up",
            plain_english_summary="Markets are rising.",
            key_drivers=["AI capex expanding"],
            active_themes=["ai_compute_infrastructure"],
            freshness_timestamp=datetime.now(UTC).isoformat(),
            confidence_label="High",
        )
        self.validate(p.to_dict())  # must not raise

    def test_blocked_field_bid_raises(self):
        payload = self.Payload().to_dict()
        payload["bid"] = 100.0
        with pytest.raises(self.Error, match="blocked fields"):
            self.validate(payload)

    def test_blocked_field_order_id_raises(self):
        payload = self.Payload().to_dict()
        payload["order_id"] = "abc123"
        with pytest.raises(self.Error, match="blocked fields"):
            self.validate(payload)

    def test_blocked_field_position_size_raises(self):
        payload = self.Payload().to_dict()
        payload["position_size"] = 500
        with pytest.raises(self.Error, match="blocked fields"):
            self.validate(payload)

    def test_blocked_field_raw_score_raises(self):
        payload = self.Payload().to_dict()
        payload["raw_score"] = 42
        with pytest.raises(self.Error, match="blocked fields"):
            self.validate(payload)

    def test_unexpected_field_raises(self):
        payload = self.Payload().to_dict()
        payload["some_internal_thing"] = "value"
        with pytest.raises(self.Error, match="approved customer-safe allowlist"):
            self.validate(payload)

    def test_execution_signal_blocked(self):
        payload = self.Payload().to_dict()
        payload["execution_signal"] = "BUY"
        with pytest.raises(self.Error):
            self.validate(payload)

    def test_to_dict_all_allowed_fields(self):
        p = self.Payload()
        d = p.to_dict()
        extra = set(d.keys()) - self.allowed
        assert not extra, f"to_dict() produced fields not in allowlist: {extra}"

    def test_default_data_entitlement_note_present(self):
        p = self.Payload()
        assert "not financial advice" in p.data_entitlement_note.lower()

    def test_blocked_fields_registry_covers_key_broker_fields(self):
        must_block = {"bid", "ask", "order_id", "position_size", "broker_account_id", "raw_score"}
        assert must_block.issubset(self.blocked)


# =============================================================================
# 4. market_now_builder
# =============================================================================

class TestMarketNowBuilder:
    """Tests for the Market Now payload builder."""

    def _make_fake_artifacts(self, tmp_path: Path) -> None:
        import json
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "intelligence").mkdir()
        (tmp_path / "data" / "live").mkdir()

        (tmp_path / "data" / "intelligence" / "live_driver_state.json").write_text(json.dumps({
            "active_drivers": ["ai_capex_growth", "geopolitical_risk_rising"],
            "blocked_conditions": [],
            "evidence": {
                "ai_capex_growth_reason": "SMH up 3.6%",
                "geopolitical_reason": "ITA leading SPY",
            },
        }))
        (tmp_path / "data" / "intelligence" / "theme_activation.json").write_text(json.dumps({
            "themes": [
                {
                    "theme_id": "data_centre_power",
                    "state": "activated",
                    "direction": "tailwind",
                    "confidence": 0.7,
                    "reason": "AI capex cycle driving data centre demand",
                },
            ],
        }))
        (tmp_path / "data" / "live" / "current_manifest.json").write_text(json.dumps({
            "market_regime": "TRENDING_UP",
            "published_at": "2026-05-24T10:00:00+00:00",
            "handoff_enabled": True,
        }))

    def test_build_market_now_produces_valid_payload(self, tmp_path, monkeypatch):
        self._make_fake_artifacts(tmp_path)
        monkeypatch.syspath_prepend(str(_REPO_ROOT))
        import market_now_builder
        monkeypatch.setattr(market_now_builder, "_BASE", str(tmp_path))
        payload = market_now_builder.build_market_now()
        assert payload.market_regime_label == "Risk-on — equities trending higher"
        assert "ai_capex_growth" in payload.key_drivers
        assert "data_centre_power" in payload.active_themes

    def test_market_now_passes_customer_safe_validation(self, tmp_path, monkeypatch):
        self._make_fake_artifacts(tmp_path)
        import market_now_builder
        from saas_intelligence_output import validate_customer_payload
        monkeypatch.setattr(market_now_builder, "_BASE", str(tmp_path))
        d = market_now_builder.get_market_now_dict()
        validate_customer_payload(d)  # must not raise

    def test_market_now_contains_no_blocked_fields(self, tmp_path, monkeypatch):
        self._make_fake_artifacts(tmp_path)
        import market_now_builder
        from saas_intelligence_output import get_blocked_fields
        monkeypatch.setattr(market_now_builder, "_BASE", str(tmp_path))
        d = market_now_builder.get_market_now_dict()
        blocked = get_blocked_fields()
        found = [k for k in d if k in blocked]
        assert not found, f"Market Now payload contains blocked fields: {found}"

    def test_market_now_degrades_gracefully_on_missing_files(self, tmp_path, monkeypatch):
        """Builder should not crash when intelligence artefacts are absent."""
        (tmp_path / "data").mkdir()
        import market_now_builder
        monkeypatch.setattr(market_now_builder, "_BASE", str(tmp_path))
        payload = market_now_builder.build_market_now()
        # Should produce a payload with defaults, not raise
        assert isinstance(payload.market_regime_label, str)
        assert isinstance(payload.plain_english_summary, str)

    def test_market_now_freshness_timestamp_is_present(self, tmp_path, monkeypatch):
        self._make_fake_artifacts(tmp_path)
        import market_now_builder
        monkeypatch.setattr(market_now_builder, "_BASE", str(tmp_path))
        payload = market_now_builder.build_market_now()
        assert payload.freshness_timestamp  # non-empty string


# =============================================================================
# 5. Execution guards — fail closed in intelligence_cloud mode
# =============================================================================

class TestExecutionGuardsIntelligenceCloud:
    """
    Verify that execute_buy, execute_sell, execute_short, execute_buy_option,
    execute_sell_option, and flatten_all all fail closed when
    DECIFER_RUNTIME_MODE=intelligence_cloud.
    """

    def _set_cloud_mode(self, monkeypatch):
        monkeypatch.setenv("DECIFER_RUNTIME_MODE", "intelligence_cloud")
        monkeypatch.delenv("DECIFER_EXECUTION_ENABLED", raising=False)
        # Force runtime_config to reload with the new environment
        if "runtime_config" in sys.modules:
            del sys.modules["runtime_config"]
        import runtime_config
        importlib.reload(runtime_config)
        return runtime_config

    def _clear_cloud_mode(self):
        for key in list(os.environ):
            if key.startswith("DECIFER_"):
                del os.environ[key]
        if "runtime_config" in sys.modules:
            del sys.modules["runtime_config"]

    def test_execute_buy_blocked_in_intelligence_cloud(self, monkeypatch):
        rc = self._set_cloud_mode(monkeypatch)
        with pytest.raises(rc.ExecutionBlockedError):
            rc.assert_execution_allowed("execute_buy")
        self._clear_cloud_mode()

    def test_execute_sell_blocked_in_intelligence_cloud(self, monkeypatch):
        rc = self._set_cloud_mode(monkeypatch)
        with pytest.raises(rc.ExecutionBlockedError):
            rc.assert_execution_allowed("execute_sell")
        self._clear_cloud_mode()

    def test_execute_short_blocked_in_intelligence_cloud(self, monkeypatch):
        rc = self._set_cloud_mode(monkeypatch)
        with pytest.raises(rc.ExecutionBlockedError):
            rc.assert_execution_allowed("execute_short")
        self._clear_cloud_mode()

    def test_execute_buy_option_blocked_in_intelligence_cloud(self, monkeypatch):
        rc = self._set_cloud_mode(monkeypatch)
        with pytest.raises(rc.ExecutionBlockedError):
            rc.assert_execution_allowed("execute_buy_option")
        self._clear_cloud_mode()

    def test_execute_sell_option_blocked_in_intelligence_cloud(self, monkeypatch):
        rc = self._set_cloud_mode(monkeypatch)
        with pytest.raises(rc.ExecutionBlockedError):
            rc.assert_execution_allowed("execute_sell_option")
        self._clear_cloud_mode()

    def test_flatten_all_blocked_in_intelligence_cloud(self, monkeypatch):
        rc = self._set_cloud_mode(monkeypatch)
        with pytest.raises(rc.ExecutionBlockedError):
            rc.assert_execution_allowed("flatten_all")
        self._clear_cloud_mode()

    def test_error_message_mentions_intelligence_cloud(self, monkeypatch):
        rc = self._set_cloud_mode(monkeypatch)
        with pytest.raises(rc.ExecutionBlockedError, match="intelligence_cloud"):
            rc.assert_execution_allowed("execute_buy")
        self._clear_cloud_mode()


# =============================================================================
# 6. Import boundary — verifier logic
# =============================================================================

class TestImportBoundaryVerifier:
    """Tests for the layer boundary verifier's detection logic."""

    def _run_check(self, source: str, path_stem: str, layer: "Layer") -> list[str]:
        """Run _check_file logic inline without invoking the full scanner."""
        import ast
        from architecture.layer_boundary import Layer, get_execution_module_names

        exec_names = get_execution_module_names()

        def parse_imports(src: str) -> set[str]:
            try:
                tree = ast.parse(src)
            except SyntaxError:
                return set()
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        names.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        names.add(node.module.split(".")[0])
            return names

        violations = []
        imports = parse_imports(source)
        if layer in (Layer.INTELLIGENCE, Layer.SAAS_OUTPUT):
            bad = imports & exec_names
            for bad_mod in sorted(bad):
                prefix = "V1" if layer == Layer.INTELLIGENCE else "V2"
                violations.append(f"[{prefix}] {path_stem}: imports execution module '{bad_mod}'")
        if "yfinance" in imports:
            violations.append(f"[V3] {path_stem}: yfinance import")
        return violations

    def test_intelligence_importing_orders_core_is_flagged(self):
        from architecture.layer_boundary import Layer
        source = "import orders_core\n\ndef score(): pass\n"
        v = self._run_check(source, "market_intelligence.py", Layer.INTELLIGENCE)
        assert any("orders_core" in x for x in v)

    def test_saas_module_importing_bot_ibkr_is_flagged(self):
        from architecture.layer_boundary import Layer
        source = "from bot_ibkr import cancel_with_reason\n"
        v = self._run_check(source, "mobile_api.py", Layer.SAAS_OUTPUT)
        assert any("bot_ibkr" in x for x in v)

    def test_execution_importing_intelligence_is_allowed(self):
        from architecture.layer_boundary import Layer
        source = "import market_intelligence\nfrom scanner import get_market_regime\n"
        v = self._run_check(source, "orders_core.py", Layer.EXECUTION)
        assert not v  # no violations — execution consuming intelligence is allowed

    def test_yfinance_in_intelligence_module_is_flagged(self):
        from architecture.layer_boundary import Layer
        source = "import yfinance as yf\ndef get_data(): return yf.download('AAPL')\n"
        v = self._run_check(source, "signals/__init__.py", Layer.INTELLIGENCE)
        assert any("V3" in x for x in v)

    def test_clean_intelligence_module_passes(self):
        from architecture.layer_boundary import Layer
        source = (
            "import json\nimport logging\nfrom alpaca_data import get_bars\n"
            "def analyse(): pass\n"
        )
        v = self._run_check(source, "live_driver_resolver.py", Layer.INTELLIGENCE)
        assert not v

    def test_clean_saas_module_passes(self):
        from architecture.layer_boundary import Layer
        source = (
            "import json\nfrom saas_intelligence_output import SaaSIntelligencePayload\n"
            "def build(): pass\n"
        )
        v = self._run_check(source, "market_now_builder.py", Layer.SAAS_OUTPUT)
        assert not v
