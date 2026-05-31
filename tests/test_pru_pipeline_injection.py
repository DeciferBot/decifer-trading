"""
tests/test_pru_pipeline_injection.py — PRU pipeline injection tests.

Verifies that:
- PRU candidates appear in the feed after _inject_pru_into_feed
- Intelligence-layer + TTG candidates already in the feed win on dedup
- Missing/stale PRU file returns 0 gracefully
- The _pru_candidate_to_feed_entry conversion is correct
"""
from __future__ import annotations

import json
import os
import tempfile
import types
import sys

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_feed(candidates: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-05-31T10:00:00Z",
        "fresh_until": "2026-06-02T10:00:00Z",
        "mode": "intelligence_advisory_feed",
        "source_files": [],
        "feed_summary": {},
        "candidates": candidates,
        "warnings": [],
    }


def _write_feed(path: str, candidates: list[dict]) -> None:
    with open(path, "w") as f:
        json.dump(_make_feed(candidates), f)


def _read_feed_candidates(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)["candidates"]


_SAMPLE_PRU_SYMBOLS = [
    {
        "ticker": "AAPL",
        "discovery_score": 7,
        "adjusted_discovery_score": 4,
        "risk_penalty_pts": -3,
        "primary_archetype": "Quality Compounder",
        "secondary_tags": ["Above 50DMA"],
        "universe_bucket": "core_research",
        "matched_position_archetypes": ["Quality Compounder"],
        "discovery_signals": ["revenue_yoy_gt_10pct"],
        "discovery_signal_points": {"revenue_yoy_gt_10pct": 3},
        "missing_data_fields": [],
        "pru_fmp_snapshot": {},
        "universe_source": "position_research",
        "scanner_tier": "D",
        "position_research_universe_member": True,
        "active_trading_universe_member": False,
        "priority_overlap": False,
        "universe_entry_reason": "strong: revenue_yoy_gt_10pct; archetype: Quality Compounder",
    },
    {
        "ticker": "MSFT",
        "discovery_score": 5,
        "adjusted_discovery_score": 5,
        "risk_penalty_pts": 0,
        "primary_archetype": "Growth Leader",
        "secondary_tags": [],
        "universe_bucket": "core_research",
        "matched_position_archetypes": ["Growth Leader"],
        "discovery_signals": ["revenue_yoy_gt_5pct"],
        "discovery_signal_points": {"revenue_yoy_gt_5pct": 2},
        "missing_data_fields": [],
        "pru_fmp_snapshot": {},
        "universe_source": "position_research",
        "scanner_tier": "D",
        "position_research_universe_member": True,
        "active_trading_universe_member": False,
        "priority_overlap": False,
        "universe_entry_reason": "archetype: Growth Leader",
    },
]


# ── Conversion tests ──────────────────────────────────────────────────────────

class TestPRUCandidateConversion:
    def test_required_fields_present(self):
        from run_intelligence_pipeline import _pru_candidate_to_feed_entry
        entry = _pru_candidate_to_feed_entry(_SAMPLE_PRU_SYMBOLS[0], "2026-06-02T10:00:00Z")
        assert entry["symbol"] == "AAPL"
        assert entry["candidate_source"] == "position_research_universe"
        assert entry["scanner_tier"] == "D"
        assert "position_research_universe" in entry["source_labels"]
        assert entry["role"] == "direct_beneficiary"
        assert "position" in entry["route_hint"]
        assert entry["primary_archetype"] == "Quality Compounder"

    def test_reason_includes_archetype(self):
        from run_intelligence_pipeline import _pru_candidate_to_feed_entry
        entry = _pru_candidate_to_feed_entry(_SAMPLE_PRU_SYMBOLS[0], "2026-06-02T10:00:00Z")
        assert "Quality Compounder" in entry["reason_to_care"]
        assert "AAPL" in entry["reason_to_care"]

    def test_confidence_is_float(self):
        from run_intelligence_pipeline import _pru_candidate_to_feed_entry
        entry = _pru_candidate_to_feed_entry(_SAMPLE_PRU_SYMBOLS[1], "2026-06-02T10:00:00Z")
        assert isinstance(entry["confidence"], float)
        assert 0 < entry["confidence"] <= 1.0


# ── Injection tests ───────────────────────────────────────────────────────────

class TestInjectPRUIntoFeed:
    def _mock_pru_module(self, symbols: list[dict]):
        """Return a fake universe_position module that yields (tickers, symbols, built_at)."""
        fake = types.ModuleType("universe_position")
        tickers = [s["ticker"] for s in symbols]
        fake.load_position_research_universe = lambda max_staleness_days=None: (
            tickers, symbols, "2026-05-31T10:00:00+00:00"
        )
        return fake

    def test_pru_candidates_appear_in_feed(self):
        from run_intelligence_pipeline import _inject_pru_into_feed

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            feed_path = f.name
        try:
            _write_feed(feed_path, [])
            fake_up = self._mock_pru_module(_SAMPLE_PRU_SYMBOLS)
            sys.modules["universe_position"] = fake_up
            try:
                count = _inject_pru_into_feed(feed_path)
            finally:
                del sys.modules["universe_position"]

            assert count == 2
            candidates = _read_feed_candidates(feed_path)
            syms = {c["symbol"] for c in candidates}
            assert "AAPL" in syms
            assert "MSFT" in syms
        finally:
            os.unlink(feed_path)

    def test_dedup_intelligence_layer_wins(self):
        """Symbols already in the feed are not overwritten by PRU."""
        from run_intelligence_pipeline import _inject_pru_into_feed

        existing = [{"symbol": "AAPL", "role": "direct_beneficiary",
                     "source_labels": ["economic_intelligence"],
                     "confidence": 0.95, "reason_to_care": "intelligence version"}]

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            feed_path = f.name
        try:
            _write_feed(feed_path, existing)
            fake_up = self._mock_pru_module(_SAMPLE_PRU_SYMBOLS)
            sys.modules["universe_position"] = fake_up
            try:
                count = _inject_pru_into_feed(feed_path)
            finally:
                del sys.modules["universe_position"]

            # AAPL already present — only MSFT should be injected
            assert count == 1
            candidates = _read_feed_candidates(feed_path)
            aapl = [c for c in candidates if c["symbol"] == "AAPL"]
            assert len(aapl) == 1
            assert aapl[0]["confidence"] == 0.95  # intelligence version preserved
            msft = [c for c in candidates if c["symbol"] == "MSFT"]
            assert len(msft) == 1
        finally:
            os.unlink(feed_path)

    def test_empty_pru_returns_zero(self):
        """Empty PRU load returns 0 without modifying feed."""
        from run_intelligence_pipeline import _inject_pru_into_feed

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            feed_path = f.name
        try:
            _write_feed(feed_path, [])
            fake_up = self._mock_pru_module([])
            sys.modules["universe_position"] = fake_up
            try:
                count = _inject_pru_into_feed(feed_path)
            finally:
                del sys.modules["universe_position"]

            assert count == 0
        finally:
            os.unlink(feed_path)

    def test_graceful_on_import_error(self):
        """If universe_position can't be imported, returns 0."""
        from run_intelligence_pipeline import _inject_pru_into_feed

        # Remove universe_position from sys.modules if present
        saved = sys.modules.pop("universe_position", None)
        import builtins
        real_import = builtins.__import__
        def _broken(name, *args, **kwargs):
            if name == "universe_position":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)
        builtins.__import__ = _broken
        try:
            count = _inject_pru_into_feed("/tmp/irrelevant.json")
        finally:
            builtins.__import__ = real_import
            if saved is not None:
                sys.modules["universe_position"] = saved
        assert count == 0

    def test_graceful_on_missing_feed(self):
        """Missing feed file returns 0."""
        from run_intelligence_pipeline import _inject_pru_into_feed

        fake_up = self._mock_pru_module(_SAMPLE_PRU_SYMBOLS)
        sys.modules["universe_position"] = fake_up
        try:
            count = _inject_pru_into_feed("/tmp/does_not_exist_pru_test_xxxxxx.json")
        finally:
            del sys.modules["universe_position"]
        assert count == 0

    def test_pru_candidate_has_scanner_tier_d(self):
        """Every PRU candidate in the feed must carry scanner_tier=D."""
        from run_intelligence_pipeline import _inject_pru_into_feed

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            feed_path = f.name
        try:
            _write_feed(feed_path, [])
            fake_up = self._mock_pru_module(_SAMPLE_PRU_SYMBOLS)
            sys.modules["universe_position"] = fake_up
            try:
                _inject_pru_into_feed(feed_path)
            finally:
                del sys.modules["universe_position"]

            candidates = _read_feed_candidates(feed_path)
            pru_candidates = [c for c in candidates if c.get("candidate_source") == "position_research_universe"]
            assert len(pru_candidates) == 2
            for c in pru_candidates:
                assert c["scanner_tier"] == "D"
        finally:
            os.unlink(feed_path)
