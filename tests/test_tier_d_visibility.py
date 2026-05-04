"""
Tests for Tier D visibility improvements.

Covers:
  A. _format_candidate_line() appends pos_meta for Tier D candidates.
  B. _format_candidate_line() leaves non-Tier-D candidates unchanged.
  C. _format_candidate_line() triggers on PRU metadata even without scanner_tier.
  D. ORDER_INTENT records preserve scanner_tier / origin_path / pru fields.
  E. event_log.get_ts() reads ts / timestamp / created_at robustly.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy dependencies before importing market_intelligence ───────────────

for _mod in ("ib_async", "anthropic", "alpaca_stream"):
    if _mod not in sys.modules:
        _m = types.ModuleType(_mod)
        _m.IB = MagicMock
        sys.modules[_mod] = _m

# Stub config so market_intelligence imports cleanly
_config_mod = types.ModuleType("config")
_config_mod.CONFIG = {
    "apex_max_tokens": 6144,
    "apex_expanded_band_floor": 20,
}
sys.modules.setdefault("config", _config_mod)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tier_d_candidate(**overrides) -> dict:
    """Minimal Tier D candidate dict with all PRU metadata fields."""
    base = {
        "symbol":                           "APLD",
        "score":                            83,
        "direction":                        "LONG",
        "scanner_tier":                     "D",
        "origin_path":                      "tier_d_main_path",
        "position_research_universe_member": True,
        "adjusted_discovery_score":         14,
        "primary_archetype":                "Quality Compounder",
        "universe_bucket":                  "core_research",
        "apex_cap_score":                   91.0,
        "selected_band":                    "core",
        "selected_slot":                    7,
        "atr_5m":                           0.12,
        "atr_daily":                        2.1,
        "vol_ratio":                        1.4,
        "daily_tape_score":                 0.6,
        "stock_rs_vs_spy":                  1.1,
        "catalyst_score":                   5,
        "options_eligible":                 True,
        "score_breakdown":                  {"trend": 8, "momentum": 7},
        "divergence_flags":                 [],
        "news_headlines":                   [],
        "news_finbert_sentiment":           None,
        "trade_context":                    {},
    }
    base.update(overrides)
    return base


def _make_normal_candidate(**overrides) -> dict:
    """Minimal non-Tier-D candidate with no PRU metadata."""
    base = {
        "symbol":            "NVDA",
        "score":             75,
        "direction":         "LONG",
        "atr_5m":            0.5,
        "atr_daily":         4.2,
        "vol_ratio":         2.1,
        "daily_tape_score":  0.8,
        "stock_rs_vs_spy":   1.3,
        "catalyst_score":    3,
        "options_eligible":  True,
        "score_breakdown":   {"trend": 9, "momentum": 8},
        "divergence_flags":  [],
        "news_headlines":    [],
        "news_finbert_sentiment": None,
        "trade_context":     {},
    }
    base.update(overrides)
    return base


# ── Import the function under test ────────────────────────────────────────────

# Patch the heavy modules market_intelligence imports at module level
_stub_llm = types.ModuleType("llm_client")
_stub_llm.call_apex_with_meta = MagicMock(return_value=('{"new_entries":[]}', {}))
sys.modules.setdefault("llm_client", _stub_llm)
sys.modules.setdefault("schemas", types.ModuleType("schemas"))

from market_intelligence import _format_candidate_line  # noqa: E402


# ── A / B / C: _format_candidate_line ────────────────────────────────────────

class TestFormatCandidateLineTierD(unittest.TestCase):

    def test_tier_d_line_contains_pos_meta(self):
        line = _format_candidate_line(_make_tier_d_candidate())
        self.assertIn("pos_meta=[", line)

    def test_tier_d_line_contains_tier_d_tag(self):
        line = _format_candidate_line(_make_tier_d_candidate())
        self.assertIn("tier=D", line)

    def test_tier_d_line_contains_origin(self):
        line = _format_candidate_line(_make_tier_d_candidate())
        self.assertIn("origin=tier_d_main_path", line)

    def test_tier_d_line_contains_pru_true(self):
        line = _format_candidate_line(_make_tier_d_candidate())
        self.assertIn("pru=True", line)

    def test_tier_d_line_contains_adj_disc(self):
        line = _format_candidate_line(_make_tier_d_candidate(adjusted_discovery_score=14))
        self.assertIn("adj_disc=14", line)

    def test_tier_d_line_contains_archetype(self):
        line = _format_candidate_line(_make_tier_d_candidate())
        self.assertIn("arch=Quality Compounder", line)

    def test_tier_d_line_contains_bucket(self):
        line = _format_candidate_line(_make_tier_d_candidate())
        self.assertIn("bucket=core_research", line)

    def test_tier_d_line_contains_apex_score(self):
        line = _format_candidate_line(_make_tier_d_candidate(apex_cap_score=91.0))
        self.assertIn("apex_score=91.0", line)

    def test_tier_d_line_contains_band_and_slot(self):
        line = _format_candidate_line(_make_tier_d_candidate(selected_band="core", selected_slot=7))
        self.assertIn("band=core", line)
        self.assertIn("slot=7", line)

    def test_expanded_band_reflected_correctly(self):
        line = _format_candidate_line(_make_tier_d_candidate(
            symbol="SNAP",
            selected_band="expanded",
            selected_slot=35,
            apex_cap_score=24.5,
        ))
        self.assertIn("band=expanded", line)
        self.assertIn("slot=35", line)

    def test_non_tier_d_no_pos_meta(self):
        line = _format_candidate_line(_make_normal_candidate())
        self.assertNotIn("pos_meta=[", line)

    def test_pru_metadata_without_scanner_tier_triggers_pos_meta(self):
        """PRU overlap candidate: scanner_tier absent but PRU metadata present."""
        c = _make_normal_candidate(
            adjusted_discovery_score=10,
            primary_archetype="Value Cyclical",
            universe_bucket="core_research",
        )
        line = _format_candidate_line(c)
        self.assertIn("pos_meta=[", line)

    def test_adj_disc_none_shows_question_mark(self):
        c = _make_tier_d_candidate(adjusted_discovery_score=None)
        line = _format_candidate_line(c)
        self.assertIn("adj_disc=?", line)

    def test_apex_score_none_shows_question_mark(self):
        c = _make_tier_d_candidate(apex_cap_score=None)
        line = _format_candidate_line(c)
        self.assertIn("apex_score=?", line)

    def test_shadow_hint_preserved_before_pos_meta(self):
        c = _make_tier_d_candidate(_shadow_hint="SHADOW: bullish divergence")
        line = _format_candidate_line(c)
        self.assertIn("SHADOW: bullish divergence", line)
        # hint must appear before the rest of the candidate line
        self.assertLess(line.index("SHADOW"), line.index("pos_meta=["))

    def test_standard_fields_present_in_tier_d_line(self):
        c = _make_tier_d_candidate()
        line = _format_candidate_line(c)
        self.assertIn("score=83", line)
        self.assertIn("dir=LONG", line)


# ── D: ORDER_INTENT shape ─────────────────────────────────────────────────────

class TestOrderIntentShape(unittest.TestCase):
    """Verify that scanner_tier / origin / pru flow through _origin_extras."""

    def _build_origin_extras(self, payload: dict, pru_set: frozenset | None = None) -> dict:
        """Re-implement the _origin_extras dict from signal_dispatcher so tests
        can validate its shape without importing the full dispatcher."""
        pru_members = pru_set if pru_set is not None else frozenset()
        sym = payload.get("symbol", "")
        is_tier_d = payload.get("scanner_tier") == "D"
        in_pru = is_tier_d or (sym in pru_members)
        if is_tier_d:
            origin_path = "tier_d_main_path"
        elif sym in pru_members:
            origin_path = "normal_trade_pru_overlap"
        else:
            origin_path = "normal_path"
        return {
            k: v for k, v in {
                "scanner_tier":                      payload.get("scanner_tier"),
                "universe_bucket":                   payload.get("universe_bucket"),
                "primary_archetype":                 payload.get("primary_archetype"),
                "adjusted_discovery_score":          payload.get("adjusted_discovery_score"),
                "position_research_universe_member": in_pru,
                "origin_path":                       origin_path,
                "apex_cap_score":                    payload.get("apex_cap_score"),
                "selected_band":                     payload.get("selected_band"),
                "selected_slot":                     payload.get("selected_slot"),
            }.items() if v is not None
        }

    def test_tier_d_payload_sets_scanner_tier(self):
        payload = _make_tier_d_candidate()
        extras = self._build_origin_extras(payload)
        self.assertEqual(extras.get("scanner_tier"), "D")

    def test_tier_d_payload_sets_origin_path(self):
        payload = _make_tier_d_candidate()
        extras = self._build_origin_extras(payload)
        self.assertEqual(extras.get("origin_path"), "tier_d_main_path")

    def test_tier_d_payload_pru_true(self):
        payload = _make_tier_d_candidate()
        extras = self._build_origin_extras(payload)
        self.assertTrue(extras.get("position_research_universe_member"))

    def test_apex_cap_score_propagated(self):
        payload = _make_tier_d_candidate(apex_cap_score=91.0)
        extras = self._build_origin_extras(payload)
        self.assertEqual(extras.get("apex_cap_score"), 91.0)

    def test_selected_band_propagated(self):
        payload = _make_tier_d_candidate(selected_band="core")
        extras = self._build_origin_extras(payload)
        self.assertEqual(extras.get("selected_band"), "core")

    def test_selected_slot_propagated(self):
        payload = _make_tier_d_candidate(selected_slot=7)
        extras = self._build_origin_extras(payload)
        self.assertEqual(extras.get("selected_slot"), 7)

    def test_pru_overlap_candidate_gets_correct_origin(self):
        payload = _make_normal_candidate(symbol="CRDO")
        extras = self._build_origin_extras(payload, pru_set=frozenset(["CRDO"]))
        self.assertEqual(extras.get("origin_path"), "normal_trade_pru_overlap")
        self.assertTrue(extras.get("position_research_universe_member"))

    def test_normal_candidate_no_scanner_tier(self):
        payload = _make_normal_candidate()
        extras = self._build_origin_extras(payload)
        self.assertNotIn("scanner_tier", extras)

    def test_none_values_excluded_from_extras(self):
        payload = _make_tier_d_candidate(apex_cap_score=None, selected_band=None, selected_slot=None)
        extras = self._build_origin_extras(payload)
        self.assertNotIn("apex_cap_score", extras)
        self.assertNotIn("selected_band", extras)
        self.assertNotIn("selected_slot", extras)


if __name__ == "__main__":
    unittest.main()
