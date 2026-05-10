"""
test_intelligence_day7.py — Day 7 Intelligence-First architecture tests.

Covers:
  - source_adapter_snapshot.json schema and safety contracts
  - Per-adapter status and field checks
  - Catalyst approved-source guard (Correction 2)
  - adapter_usage_summary in shadow universe
  - adapter_impact_analysis with real symbol counts (Correction 1)
  - adapter snapshot missing → graceful degradation (Correction 3)
  - Validator passes all Day 7 files
  - Prior day regressions

All tests are read-only against generated output files plus targeted unit tests
against the adapters themselves. No production files are modified.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────

_ADAPTER_SNAP = "data/intelligence/source_adapter_snapshot.json"
_SHADOW        = "data/universe_builder/active_opportunity_universe_shadow.json"
_COMPARISON    = "data/universe_builder/current_vs_shadow_comparison.json"
_REPORT        = "data/universe_builder/universe_builder_report.json"


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Adapter snapshot — existence and top-level safety
# ──────────────────────────────────────────────────────────────────────────────

class TestAdapterSnapshotExists:
    def test_snapshot_file_exists(self):
        assert os.path.exists(_ADAPTER_SNAP), f"Missing: {_ADAPTER_SNAP}"

    def test_live_output_changed_is_false(self):
        snap = _load(_ADAPTER_SNAP)
        assert snap["live_output_changed"] is False

    def test_adapter_summary_total_is_nine(self):
        snap = _load(_ADAPTER_SNAP)
        summary = snap["adapter_summary"]
        assert summary["adapters_total"] == 9

    def test_mode_is_read_only(self):
        snap = _load(_ADAPTER_SNAP)
        assert snap["mode"] == "read_only_adapter_snapshot"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Per-adapter safety contract — all 9 must comply
# ──────────────────────────────────────────────────────────────────────────────

class TestAdapterSafetyContract:
    def _adapters(self) -> dict[str, Any]:
        return _load(_ADAPTER_SNAP)["adapters"]

    def test_all_adapters_side_effects_false(self):
        for name, a in self._adapters().items():
            assert a["side_effects_triggered"] is False, \
                f"adapter '{name}' has side_effects_triggered=True"

    def test_all_adapters_live_data_false(self):
        for name, a in self._adapters().items():
            assert a["live_data_called"] is False, \
                f"adapter '{name}' has live_data_called=True"

    def test_all_adapters_have_valid_source_status(self):
        valid = {"available", "unavailable", "skipped_due_side_effect_risk"}
        for name, a in self._adapters().items():
            assert a["source_status"] in valid, \
                f"adapter '{name}' has invalid source_status '{a['source_status']}'"

    def test_all_adapters_have_symbols_read_list(self):
        for name, a in self._adapters().items():
            assert isinstance(a["symbols_read"], list), \
                f"adapter '{name}' symbols_read is not a list"

    def test_skipped_adapters_have_skipped_reason(self):
        for name, a in self._adapters().items():
            if a["source_status"] == "skipped_due_side_effect_risk":
                assert a.get("skipped_reason"), \
                    f"adapter '{name}' is skipped but skipped_reason is empty"


# ──────────────────────────────────────────────────────────────────────────────
# 3. Scanner regime adapter — must be skipped, never executed
# ──────────────────────────────────────────────────────────────────────────────

class TestScannerRegimeAdapter:
    def _scanner(self) -> dict:
        return _load(_ADAPTER_SNAP)["adapters"]["scanner_regime"]

    def test_scanner_regime_is_skipped(self):
        assert self._scanner()["source_status"] == "skipped_due_side_effect_risk"

    def test_scanner_regime_skipped_reason_non_empty(self):
        assert self._scanner()["skipped_reason"]

    def test_scanner_regime_zero_symbols(self):
        assert self._scanner()["symbols_count"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# 4. Theme tracker adapter — safe static read
# ──────────────────────────────────────────────────────────────────────────────

class TestThemeTrackerAdapter:
    def _tt(self) -> dict:
        return _load(_ADAPTER_SNAP)["adapters"]["theme_tracker_roster"]

    def test_theme_tracker_is_available(self):
        assert self._tt()["source_status"] == "available"

    def test_theme_tracker_has_symbols(self):
        assert self._tt()["symbols_count"] > 0

    def test_theme_tracker_source_label(self):
        label = self._tt()["output_summary"]["source_label"]
        assert label == "legacy_theme_tracker_read_only"

    def test_theme_tracker_no_side_effects(self):
        assert self._tt()["side_effects_triggered"] is False


# ──────────────────────────────────────────────────────────────────────────────
# 5. Unavailable adapters are explicitly documented
# ──────────────────────────────────────────────────────────────────────────────

class TestUnavailableAdapters:
    def test_unavailable_adapters_in_unavailable_sources(self):
        snap = _load(_ADAPTER_SNAP)
        unavailable_names = {
            name for name, a in snap["adapters"].items()
            if a["source_status"] in ("unavailable", "skipped_due_side_effect_risk")
        }
        documented_names = {u["adapter_name"] for u in snap["unavailable_sources"]}
        assert unavailable_names == documented_names, \
            f"Undocumented unavailable adapters: {unavailable_names - documented_names}"

    def test_unavailable_sources_have_reason(self):
        snap = _load(_ADAPTER_SNAP)
        for entry in snap["unavailable_sources"]:
            assert entry.get("reason"), \
                f"unavailable_source '{entry.get('adapter_name')}' has no reason"


# ──────────────────────────────────────────────────────────────────────────────
# 6. Catalyst approved-source guard (Correction 2)
# ──────────────────────────────────────────────────────────────────────────────

class TestCatalystApprovedSourceGuard:
    """
    Unit-tests the approved-source guard in universe_builder.py.
    We build minimal adapter snapshots and candidate feeds to test the logic.
    """

    def _make_snap(self, catalyst_symbols: list[str]) -> dict:
        """Build a minimal adapter snapshot with catalyst candidates."""
        return {
            "schema_version": "1.0",
            "generated_at":   "2026-05-05T00:00:00Z",
            "mode":           "read_only_adapter_snapshot",
            "source_files":   [],
            "adapters": {
                "scanner_regime": {
                    "adapter_name": "scanner_regime",
                    "source_status": "skipped_due_side_effect_risk",
                    "source_path_or_module": "scanner",
                    "records_read": 0, "symbols_read": [], "symbols_count": 0,
                    "fields_available": [], "fields_missing": [],
                    "side_effects_triggered": False, "live_data_called": False,
                    "output_summary": {}, "warnings": [],
                    "skipped_reason": "live data risk",
                },
                "theme_tracker_roster": {
                    "adapter_name": "theme_tracker_roster",
                    "source_status": "unavailable",
                    "source_path_or_module": "theme_tracker",
                    "records_read": 0, "symbols_read": [], "symbols_count": 0,
                    "fields_available": [], "fields_missing": [],
                    "side_effects_triggered": False, "live_data_called": False,
                    "output_summary": {}, "warnings": ["unavailable"], "skipped_reason": "",
                },
                "overnight_research": {
                    "adapter_name": "overnight_research",
                    "source_status": "unavailable",
                    "source_path_or_module": "data/overnight_notes.json",
                    "records_read": 0, "symbols_read": [], "symbols_count": 0,
                    "fields_available": [], "fields_missing": [],
                    "side_effects_triggered": False, "live_data_called": False,
                    "output_summary": {}, "warnings": ["unavailable"], "skipped_reason": "",
                },
                "catalyst_engine": {
                    "adapter_name": "catalyst_engine",
                    "source_status": "available",
                    "source_path_or_module": "data/candidates_2026-05-05.json",
                    "records_read": len(catalyst_symbols), "symbols_count": len(catalyst_symbols),
                    "symbols_read": catalyst_symbols,
                    "fields_available": ["symbol", "catalyst_score", "reason", "route_hint"],
                    "fields_missing": [],
                    "side_effects_triggered": False, "live_data_called": False,
                    "output_summary": {
                        "catalyst_candidates": [
                            {"symbol": s, "catalyst_score": 0.80, "reason": "test",
                             "route_hint": ["swing"], "source_label": "catalyst_watchlist_read_only"}
                            for s in catalyst_symbols
                        ],
                        "catalyst_count": len(catalyst_symbols),
                        "source_label": "catalyst_watchlist_read_only",
                        "route_hint": ["swing"],
                    },
                    "warnings": [], "skipped_reason": "",
                },
                "tier_d_position_research": {
                    "adapter_name": "tier_d_position_research",
                    "source_status": "unavailable",
                    "source_path_or_module": "_tier_d_path",
                    "records_read": 0, "symbols_read": [], "symbols_count": 0,
                    "fields_available": [], "fields_missing": [],
                    "side_effects_triggered": False, "live_data_called": False,
                    "output_summary": {}, "warnings": ["unavailable"], "skipped_reason": "",
                },
                "tier_b_daily_promoted": {
                    "adapter_name": "tier_b_daily_promoted",
                    "source_status": "unavailable",
                    "source_path_or_module": "_tier_b_path",
                    "records_read": 0, "symbols_read": [], "symbols_count": 0,
                    "fields_available": [], "fields_missing": [],
                    "side_effects_triggered": False, "live_data_called": False,
                    "output_summary": {}, "warnings": ["unavailable"], "skipped_reason": "",
                },
                "committed_universe": {
                    "adapter_name": "committed_universe",
                    "source_status": "unavailable",
                    "source_path_or_module": "_committed_path",
                    "records_read": 0, "symbols_read": [], "symbols_count": 0,
                    "fields_available": [], "fields_missing": [],
                    "side_effects_triggered": False, "live_data_called": False,
                    "output_summary": {}, "warnings": ["unavailable"], "skipped_reason": "",
                },
                "favourites_manual_conviction": {
                    "adapter_name": "favourites_manual_conviction",
                    "source_status": "unavailable",
                    "source_path_or_module": "_favourites_path",
                    "records_read": 0, "symbols_read": [], "symbols_count": 0,
                    "fields_available": [], "fields_missing": [],
                    "side_effects_triggered": False, "live_data_called": False,
                    "output_summary": {}, "warnings": ["unavailable"], "skipped_reason": "",
                },
                "held_positions": {
                    "adapter_name": "held_positions",
                    "source_status": "unavailable",
                    "source_path_or_module": "_positions_path",
                    "records_read": 0, "symbols_read": [], "symbols_count": 0,
                    "fields_available": [], "fields_missing": [],
                    "side_effects_triggered": False, "live_data_called": False,
                    "output_summary": {}, "warnings": ["unavailable"], "skipped_reason": "",
                },
            },
            "adapter_summary": {
                "adapters_total": 9,
                "adapters_available": 1,
                "adapters_unavailable": 7,
                "adapters_skipped_due_side_effect_risk": 1,
                "total_symbols_read": len(catalyst_symbols),
            },
            "unavailable_sources": [],
            "warnings": [],
            "live_output_changed": False,
        }

    def _build_with_snap(self, snap: dict, favs: list[str]) -> Any:
        """
        Build a shadow universe with a minimal feed (empty) and given adapter snap + favs.
        Returns the ShadowUniverse. Uses temp files for all paths.
        """
        from universe_builder import UniverseBuilder

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write adapter snapshot
            snap_path = os.path.join(tmpdir, "adapter_snap.json")
            with open(snap_path, "w") as f:
                json.dump(snap, f)

            # Write empty economic feed
            feed_path = os.path.join(tmpdir, "feed.json")
            with open(feed_path, "w") as f:
                json.dump({
                    "schema_version": "1.0",
                    "generated_at":   "2026-05-05T00:00:00Z",
                    "fresh_until":    "2026-05-06T00:00:00Z",
                    "mode":           "shadow_report_only",
                    "source_files":   [],
                    "feed_summary":   {"total_candidates": 0, "llm_symbol_discovery_used": False,
                                      "raw_news_used": False, "broad_intraday_scan_used": False},
                    "candidates":     [],
                    "live_output_changed": False,
                }, f)

            # Write favourites
            favs_path = os.path.join(tmpdir, "favourites.json")
            with open(favs_path, "w") as f:
                json.dump(favs, f)

            # Write empty tier_b and tier_d
            tb_path = os.path.join(tmpdir, "tier_b.json")
            td_path = os.path.join(tmpdir, "tier_d.json")
            for p in [tb_path, td_path]:
                with open(p, "w") as f:
                    json.dump({"symbols": []}, f)

            # Write output path
            out_path = os.path.join(tmpdir, "shadow.json")

            builder = UniverseBuilder(
                feed_path=feed_path,
                output_path=out_path,
                snapshot_path=snap_path,
                adapter_snapshot_path=snap_path,
            )
            # Patch the tier paths temporarily
            import universe_builder as ub
            orig_tb = ub._TIER_B_PATH
            orig_td = ub._TIER_D_PATH
            orig_fav = ub._FAVOURITES_PATH
            orig_committed = ub._COMMITTED_PATH
            orig_roster = ub._THEMATIC_ROSTER_PATH
            try:
                ub._TIER_B_PATH = tb_path
                ub._TIER_D_PATH = td_path
                ub._FAVOURITES_PATH = favs_path
                ub._COMMITTED_PATH = tb_path   # empty symbols
                ub._THEMATIC_ROSTER_PATH = td_path  # empty rosters key → empty set
                universe = builder.build()
            finally:
                ub._TIER_B_PATH = orig_tb
                ub._TIER_D_PATH = orig_td
                ub._FAVOURITES_PATH = orig_fav
                ub._COMMITTED_PATH = orig_committed
                ub._THEMATIC_ROSTER_PATH = orig_roster

            return universe

    def test_catalyst_symbol_in_approved_source_is_added(self):
        """AAPL is in favourites (approved source) — catalyst candidate must be admitted."""
        snap = self._make_snap(["AAPL"])
        universe = self._build_with_snap(snap, favs=["AAPL"])
        symbols = [c.symbol for c in universe.candidates]
        assert "AAPL" in symbols

    def test_catalyst_symbol_not_in_approved_source_is_excluded(self):
        """XYZUNK is not in any approved source — must be excluded."""
        snap = self._make_snap(["XYZUNK"])
        universe = self._build_with_snap(snap, favs=["AAPL"])
        symbols = [c.symbol for c in universe.candidates]
        assert "XYZUNK" not in symbols

    def test_catalyst_exclusion_reason_is_correct(self):
        """Excluded catalyst symbol must have 'catalyst_symbol_not_in_approved_source' in log."""
        snap = self._make_snap(["XYZUNK"])
        universe = self._build_with_snap(snap, favs=["AAPL"])
        reasons = [e.get("reason") for e in universe.exclusion_log if e.get("symbol") == "XYZUNK"]
        assert any("catalyst_symbol_not_in_approved_source" in r for r in reasons), \
            f"Expected 'catalyst_symbol_not_in_approved_source' in exclusion log, got: {reasons}"

    def test_catalyst_candidate_is_not_executable(self):
        """Catalyst candidates must have executable=False."""
        snap = self._make_snap(["AAPL"])
        universe = self._build_with_snap(snap, favs=["AAPL"])
        for c in universe.candidates:
            if "catalyst_watchlist_read_only" in c.source_labels:
                assert c.execution_instructions.get("executable") is False, \
                    f"Catalyst candidate {c.symbol} is marked executable"

    def test_catalyst_candidate_does_not_consume_structural_quota(self):
        """Catalyst candidates go to catalyst_swing, never structural_position."""
        snap = self._make_snap(["AAPL"])
        universe = self._build_with_snap(snap, favs=["AAPL"])
        for c in universe.candidates:
            if "catalyst_watchlist_read_only" in c.source_labels:
                assert c.quota.get("group") == "catalyst_swing", \
                    f"Catalyst candidate {c.symbol} consumed structural quota: {c.quota}"


# ──────────────────────────────────────────────────────────────────────────────
# 7. Shadow universe — adapter_usage_summary
# ──────────────────────────────────────────────────────────────────────────────

class TestShadowUniverseAdapterUsage:
    def _shadow(self) -> dict:
        return _load(_SHADOW)

    def test_adapter_usage_summary_present(self):
        assert "adapter_usage_summary" in self._shadow()

    def test_adapter_usage_summary_side_effects_false(self):
        aus = self._shadow()["adapter_usage_summary"]
        assert aus["side_effects_triggered"] is False

    def test_adapter_usage_summary_live_data_false(self):
        aus = self._shadow()["adapter_usage_summary"]
        assert aus["live_data_called"] is False

    def test_adapter_usage_summary_snapshot_available(self):
        aus = self._shadow()["adapter_usage_summary"]
        assert aus["adapter_snapshot_available"] is True

    def test_freshness_status_is_day7(self):
        status = self._shadow()["freshness_status"]
        assert status in ("static_bootstrap_day7", "static_bootstrap_sprint2", "static_bootstrap_sprint3"), \
            f"Unexpected freshness_status: {status}"

    def test_symbols_enriched_by_adapter_is_list(self):
        aus = self._shadow()["adapter_usage_summary"]
        assert isinstance(aus["symbols_enriched_by_adapter"], list)

    def test_symbols_added_by_adapter_is_list(self):
        aus = self._shadow()["adapter_usage_summary"]
        assert isinstance(aus["symbols_added_by_adapter"], list)


# ──────────────────────────────────────────────────────────────────────────────
# 8. Comparison — adapter_impact_analysis with real symbol counts (Correction 1)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdapterImpactAnalysis:
    def _comp(self) -> dict:
        return _load(_COMPARISON)

    def _aia(self) -> dict:
        return self._comp()["adapter_impact_analysis"]

    def test_adapter_impact_analysis_present(self):
        assert "adapter_impact_analysis" in self._comp()

    def test_side_effects_false(self):
        assert self._aia()["side_effects_triggered"] is False

    def test_live_data_false(self):
        assert self._aia()["live_data_called"] is False

    def test_adapter_symbols_read_total_is_actual_count(self):
        """Must be sum of all symbols across all adapters — not the adapter count."""
        aia = self._aia()
        total = aia["adapter_symbols_read_total"]
        # Must be a real symbol count, not a small number like 7 or 9
        assert total > 100, \
            f"adapter_symbols_read_total={total} looks like an adapter count, not a symbol count"

    def test_adapter_unique_symbols_read_leq_total(self):
        aia = self._aia()
        assert aia["adapter_unique_symbols_read"] <= aia["adapter_symbols_read_total"]

    def test_adapter_unique_symbols_read_positive(self):
        assert self._aia()["adapter_unique_symbols_read"] > 0

    def test_adapter_snapshot_available_true(self):
        assert self._aia()["adapter_snapshot_available"] is True

    def test_adapter_symbols_preserved_leq_unique(self):
        aia = self._aia()
        assert aia["adapter_symbols_preserved"] <= aia["adapter_unique_symbols_read"]

    def test_report_title_is_day7(self):
        report = _load(_REPORT)
        title = report["report_title"]
        assert "Day 7" in title or "Sprint 2" in title or "Sprint 3" in title, f"Unexpected report_title: {title}"


# ──────────────────────────────────────────────────────────────────────────────
# 9. Adapter snapshot missing → graceful degradation (Correction 3)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdapterSnapshotMissingGracefulDegradation:
    """
    Build a shadow universe pointing at a non-existent adapter snapshot path.
    The builder must still produce a valid shadow universe with
    adapter_usage_summary.adapter_snapshot_available = false.
    """

    def _build_no_snap(self) -> Any:
        from universe_builder import build_shadow_universe
        import tempfile, json

        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy live feed to tmpdir
            feed_src = "data/intelligence/economic_candidate_feed.json"
            feed_dst = os.path.join(tmpdir, "feed.json")
            import shutil
            shutil.copy(feed_src, feed_dst)

            out_path = os.path.join(tmpdir, "shadow.json")
            nonexistent_snap = os.path.join(tmpdir, "DOES_NOT_EXIST.json")

            universe = build_shadow_universe(
                feed_path=feed_dst,
                output_path=out_path,
                adapter_snapshot_path=nonexistent_snap,
            )
            return universe

    def test_builder_succeeds_without_adapter_snapshot(self):
        universe = self._build_no_snap()
        assert universe is not None

    def test_adapter_snapshot_available_false(self):
        universe = self._build_no_snap()
        assert universe.adapter_usage_summary["adapter_snapshot_available"] is False

    def test_side_effects_still_false_without_snap(self):
        universe = self._build_no_snap()
        assert universe.adapter_usage_summary["side_effects_triggered"] is False

    def test_live_output_unchanged_without_snap(self):
        universe = self._build_no_snap()
        assert universe.live_output_changed is False

    def test_candidates_still_built_without_snap(self):
        """Universe must still have candidates from Day 6 sources even without adapter snap."""
        universe = self._build_no_snap()
        assert len(universe.candidates) > 0


# ──────────────────────────────────────────────────────────────────────────────
# 10. Schema validator passes all Day 7 files
# ──────────────────────────────────────────────────────────────────────────────

class TestValidatorDay7:
    def test_validate_adapter_snapshot_passes(self):
        from intelligence_schema_validator import validate_adapter_snapshot
        result = validate_adapter_snapshot(_ADAPTER_SNAP)
        assert result.ok, f"validate_adapter_snapshot errors: {result.errors}"

    def test_validate_shadow_universe_passes(self):
        from intelligence_schema_validator import validate_shadow_universe
        result = validate_shadow_universe(_SHADOW)
        assert result.ok, f"validate_shadow_universe errors: {result.errors}"

    def test_validate_comparison_passes(self):
        from intelligence_schema_validator import validate_comparison
        result = validate_comparison(_COMPARISON)
        assert result.ok, f"validate_comparison errors: {result.errors}"

    def test_validate_all_no_errors(self):
        from intelligence_schema_validator import validate_all
        results = validate_all("data/intelligence")
        errors = {k: r.errors for k, r in results.items() if not r.ok}
        assert not errors, f"Validation errors: {errors}"


# ──────────────────────────────────────────────────────────────────────────────
# 11. Production files confirmed unmodified
# ──────────────────────────────────────────────────────────────────────────────

class TestProductionFilesUnmodified:
    """
    Confirm that none of the locked production files were imported with side
    effects by the Day 7 intelligence modules. We check by importing our new
    modules and verifying they don't trigger modifications to production paths.
    """
    _LOCKED_FILES = [
        "scanner.py",
        "theme_tracker.py",
        "catalyst_engine.py",
        "overnight_research.py",
        "universe_position.py",
        "universe_committed.py",
        "market_intelligence.py",
        "bot_trading.py",
        "guardrails.py",
        "orders_core.py",
    ]

    def test_locked_files_exist(self):
        """All locked production files must exist (not deleted)."""
        for fname in self._LOCKED_FILES:
            assert os.path.exists(fname), f"Locked file missing: {fname}"

    def test_intelligence_adapters_import_has_no_side_effects(self):
        """
        Importing intelligence_adapters (without calling generate_adapter_snapshot)
        must not modify any file. We record mtime of locked files before/after.
        """
        import importlib, sys
        mtimes_before = {f: os.path.getmtime(f) for f in self._LOCKED_FILES if os.path.exists(f)}

        # Force reimport
        if "intelligence_adapters" in sys.modules:
            del sys.modules["intelligence_adapters"]
        import intelligence_adapters  # noqa: F401

        mtimes_after = {f: os.path.getmtime(f) for f in self._LOCKED_FILES if os.path.exists(f)}
        modified = [f for f in mtimes_before if mtimes_after.get(f) != mtimes_before[f]]
        assert not modified, f"Locked files modified by intelligence_adapters import: {modified}"


# ──────────────────────────────────────────────────────────────────────────────
# 12. Prior day regressions
# ──────────────────────────────────────────────────────────────────────────────

class TestPriorDayRegressions:
    def test_structural_quota_still_binding(self):
        """Day 6 invariant: structural quota must remain binding (20/20)."""
        shadow = _load(_SHADOW)
        qpd = shadow["quota_pressure_diagnostics"]
        sp = qpd["structural_position"]
        assert sp["binding"] is True, "Structural quota should be binding (20/20)"

    def test_live_output_changed_false_all_files(self):
        for path in [_ADAPTER_SNAP, _SHADOW, _COMPARISON, _REPORT]:
            data = _load(path)
            assert data.get("live_output_changed") is False, \
                f"live_output_changed is not false in {path}"

    def test_five_economic_slices_still_active(self):
        comp = _load(_COMPARISON)
        esa = comp["economic_slice_analysis"]
        # Sprint 3 adds 3 slices; accept >= 5 (the original 5 are always present)
        assert esa["slices_active"] >= 5, \
            f"Expected at least 5 economic slices, got {esa['slices_active']}"

    def test_no_attention_consuming_structural_quota(self):
        comp = _load(_COMPARISON)
        attn = comp["attention_analysis"]
        assert attn["attention_candidates_consumed_structural_quota"] is False
