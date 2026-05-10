"""
Sprint 7A.3 — Factor Registry + Provider Capability Audit tests

30 tests covering:
  - factor_registry.json structure and invariants (10)
  - provider_capability_matrix.json (4)
  - provider_fetch_test_results.json safety invariants (5)
  - layer_factor_map.json (4)
  - data_quality_report.json (3)
  - validator coverage (4)
"""

from __future__ import annotations

import json
import os
import copy
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REF_DIR = os.path.join(_HERE, "data", "reference")


def _load(filename: str) -> dict:
    path = os.path.join(_REF_DIR, filename)
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Factor registry (factor_registry.json)
# ---------------------------------------------------------------------------

class TestFactorRegistry(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = _load("factor_registry.json")

    def test_schema_version_present(self):
        self.assertIn("schema_version", self.data)

    def test_safety_flags_all_false(self):
        for flag in ("live_output_changed", "llm_called", "live_api_called", "env_inspected"):
            self.assertIs(self.data[flag], False, f"{flag} must be false")

    def test_factor_count_matches_list(self):
        self.assertEqual(self.data["total_factors"], len(self.data["factors"]))

    def test_at_least_60_factors(self):
        self.assertGreaterEqual(len(self.data["factors"]), 60)

    def test_all_factors_have_required_keys(self):
        required = {
            "factor_id", "factor_name", "category", "owning_layer",
            "consuming_layers", "providers", "primary_provider",
            "production_runtime_allowed", "offline_job_allowed",
            "update_frequency", "freshness_sla", "must_not_trigger_trade_directly",
        }
        for f in self.data["factors"]:
            for key in required:
                self.assertIn(key, f, f"factor '{f.get('factor_id')}' missing key '{key}'")

    def test_must_not_trigger_trade_directly_always_true(self):
        for f in self.data["factors"]:
            self.assertIs(
                f["must_not_trigger_trade_directly"], True,
                f"factor '{f['factor_id']}': must_not_trigger_trade_directly must be true"
            )

    def test_no_duplicate_factor_ids(self):
        ids = [f["factor_id"] for f in self.data["factors"]]
        self.assertEqual(len(ids), len(set(ids)), "duplicate factor_ids found")

    def test_all_13_categories_present(self):
        expected = {
            "reference_symbol_identity", "price_ohlcv", "technical_indicators",
            "liquidity_microstructure", "options_data", "fundamentals",
            "earnings_events", "analyst_actions", "news", "macro_economic",
            "sector_industry_theme", "ownership_short_flow", "risk_portfolio_broker",
        }
        actual = set(f["category"] for f in self.data["factors"])
        for cat in expected:
            self.assertIn(cat, actual, f"missing category '{cat}'")

    def test_providers_list_is_list(self):
        for f in self.data["factors"]:
            self.assertIsInstance(
                f["providers"], list,
                f"factor '{f['factor_id']}': providers must be a list"
            )

    def test_consuming_layers_list_is_list(self):
        for f in self.data["factors"]:
            self.assertIsInstance(
                f["consuming_layers"], list,
                f"factor '{f['factor_id']}': consuming_layers must be a list"
            )


# ---------------------------------------------------------------------------
# Provider capability matrix (provider_capability_matrix.json)
# ---------------------------------------------------------------------------

class TestProviderCapabilityMatrix(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = _load("provider_capability_matrix.json")

    def test_live_output_changed_false(self):
        self.assertIs(self.data["live_output_changed"], False)

    def test_provider_count_matches_list(self):
        self.assertEqual(self.data["provider_count"], len(self.data["providers"]))

    def test_all_6_providers_present(self):
        expected = {"alpaca", "fmp", "alpha_vantage", "yfinance", "ibkr", "local_files"}
        actual = {p["provider_name"] for p in self.data["providers"]}
        for prov in expected:
            self.assertIn(prov, actual, f"missing provider '{prov}'")

    def test_all_tiers_are_valid(self):
        valid = {
            "primary_candidate", "secondary_candidate", "fallback_only",
            "research_only", "not_suitable",
        }
        for prov in self.data["providers"]:
            for cap in prov.get("capabilities", []):
                # field is production_suitability in the generated output
                tier = cap.get("production_suitability", cap.get("tier", ""))
                self.assertIn(
                    tier, valid,
                    f"provider '{prov['provider_name']}': invalid production_suitability '{tier}'"
                )


# ---------------------------------------------------------------------------
# Provider fetch test results (provider_fetch_test_results.json)
# ---------------------------------------------------------------------------

class TestProviderFetchTestResults(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = _load("provider_fetch_test_results.json")

    def test_safety_block_must_be_false_flags(self):
        safety = self.data["safety"]
        must_be_false = (
            "trading_api_called",
            "broker_order_api_called",
            "broker_account_api_called",
            "broker_position_api_called",
            "broker_execution_api_called",
            "ibkr_order_account_position_calls",
            "env_values_logged",
            "secrets_exposed",
            "live_output_changed",
        )
        for flag in must_be_false:
            self.assertIn(flag, safety, f"safety block missing flag '{flag}'")
            self.assertIs(safety[flag], False, f"safety.{flag} must be false")

    def test_safety_block_data_provider_api_called_true(self):
        # data-provider fetches were attempted — this should be true
        self.assertIs(self.data["safety"]["data_provider_api_called"], True)

    def test_safety_block_ibkr_connection_attempted_true(self):
        # IBKR TCP probe was attempted
        self.assertIs(self.data["safety"]["ibkr_market_data_connection_attempted"], True)

    def test_per_result_secrets_never_exposed(self):
        for res in self.data["results"]:
            self.assertIs(res["secrets_exposed"], False,
                          f"result '{res['endpoint']}': secrets_exposed must be false")

    def test_per_result_live_output_never_changed(self):
        for res in self.data["results"]:
            self.assertIs(res["live_output_changed"], False,
                          f"result '{res['endpoint']}': live_output_changed must be false")

    def test_alpaca_all_pass(self):
        alpaca = [r for r in self.data["results"] if r["provider"] == "alpaca"]
        self.assertTrue(len(alpaca) > 0, "no alpaca results")
        failed = [r for r in alpaca if not r["success"]]
        self.assertEqual(
            failed, [],
            f"alpaca tests failed: {[r['endpoint'] for r in failed]}"
        )

    def test_fmp_all_pass(self):
        fmp = [r for r in self.data["results"] if r["provider"] == "fmp"]
        self.assertTrue(len(fmp) > 0, "no fmp results")
        failed = [r for r in fmp if not r["success"]]
        self.assertEqual(
            failed, [],
            f"fmp tests failed: {[r['endpoint'] for r in failed]}"
        )


# ---------------------------------------------------------------------------
# Layer factor map (layer_factor_map.json)
# ---------------------------------------------------------------------------

class TestLayerFactorMap(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = _load("layer_factor_map.json")

    def test_live_output_changed_false(self):
        self.assertIs(self.data["live_output_changed"], False)

    def test_at_least_8_layers(self):
        self.assertGreaterEqual(len(self.data["layers"]), 8)

    def test_each_layer_has_factor_count_matching_list(self):
        # layers is a list of dicts with layer_id, layer_name, factor_ids, factor_count
        for layer_data in self.data["layers"]:
            name = layer_data.get("layer_name", layer_data.get("layer_id", ""))
            self.assertEqual(
                layer_data["factor_count"], len(layer_data["factor_ids"]),
                f"layer '{name}': factor_count mismatch"
            )

    def test_each_layer_factors_is_list(self):
        for layer_data in self.data["layers"]:
            name = layer_data.get("layer_name", layer_data.get("layer_id", ""))
            self.assertIsInstance(
                layer_data["factor_ids"], list,
                f"layer '{name}': factor_ids must be a list"
            )


# ---------------------------------------------------------------------------
# Data quality report (data_quality_report.json)
# ---------------------------------------------------------------------------

class TestDataQualityReport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = _load("data_quality_report.json")

    def test_safety_flags_all_false(self):
        # Static generator — no API calls, no credential access
        for flag in (
            "live_output_changed", "data_provider_api_called", "live_trading_api_called",
            "env_values_logged", "secrets_exposed",
        ):
            self.assertIn(flag, self.data, f"data_quality_report missing flag '{flag}'")
            self.assertIs(self.data[flag], False, f"{flag} must be false")

    def test_category_counts_present(self):
        # These are counts (int) in the generated output
        for key in ("production_ready_categories", "partial_categories", "unavailable_categories"):
            self.assertIsInstance(self.data[key], int, f"{key} must be an int")
            self.assertGreaterEqual(self.data[key], 0, f"{key} must be >= 0")

    def test_provider_summary_is_dict(self):
        self.assertIsInstance(self.data["provider_summary"], dict)


# ---------------------------------------------------------------------------
# Validator coverage (intelligence_schema_validator.py)
# ---------------------------------------------------------------------------

class TestFactorRegistryValidator(unittest.TestCase):

    def _validate(self, filename: str, fn_name: str):
        from intelligence_schema_validator import (
            validate_factor_registry,
            validate_provider_capability_matrix,
            validate_provider_fetch_test_results,
            validate_layer_factor_map,
            validate_data_quality_report,
        )
        fns = {
            "validate_factor_registry": validate_factor_registry,
            "validate_provider_capability_matrix": validate_provider_capability_matrix,
            "validate_provider_fetch_test_results": validate_provider_fetch_test_results,
            "validate_layer_factor_map": validate_layer_factor_map,
            "validate_data_quality_report": validate_data_quality_report,
        }
        path = os.path.join(_REF_DIR, filename)
        return fns[fn_name](path)

    def test_factor_registry_passes_validator(self):
        result = self._validate("factor_registry.json", "validate_factor_registry")
        self.assertTrue(result.ok, f"validation errors: {result.errors}")

    def test_provider_capability_matrix_passes_validator(self):
        result = self._validate("provider_capability_matrix.json", "validate_provider_capability_matrix")
        self.assertTrue(result.ok, f"validation errors: {result.errors}")

    def test_provider_fetch_test_results_passes_validator(self):
        result = self._validate("provider_fetch_test_results.json", "validate_provider_fetch_test_results")
        self.assertTrue(result.ok, f"validation errors: {result.errors}")

    def test_layer_factor_map_passes_validator(self):
        result = self._validate("layer_factor_map.json", "validate_layer_factor_map")
        self.assertTrue(result.ok, f"validation errors: {result.errors}")

    # data_quality_report tested via validate_all in integration below

    def test_invalid_must_not_trigger_rejected(self):
        from intelligence_schema_validator import validate_factor_registry
        import tempfile
        data = _load("factor_registry.json")
        bad = copy.deepcopy(data)
        bad["factors"][0]["must_not_trigger_trade_directly"] = False
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bad, f)
            path = f.name
        result = validate_factor_registry(path)
        os.unlink(path)
        self.assertFalse(result.ok)
        self.assertTrue(any("must_not_trigger_trade_directly" in e for e in result.errors))

    def test_validate_all_includes_factor_registry_files(self):
        from intelligence_schema_validator import validate_all
        results = validate_all()
        for key in (
            "factor_registry",
            "provider_capability_matrix",
            "provider_fetch_test_results",
            "layer_factor_map",
            "data_quality_report",
        ):
            self.assertIn(key, results, f"validate_all missing key '{key}'")
            self.assertTrue(results[key].ok, f"validate_all['{key}'] failed: {results[key].errors}")


if __name__ == "__main__":
    unittest.main()
