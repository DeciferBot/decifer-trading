"""
tests/test_ttg_activation.py — TTG pipeline injection tests.

Verifies that:
- TTG candidates appear in the feed after _inject_ttg_into_feed
- Symbols already in the feed (intelligence layer) are not overwritten
- Symbols with status='needs_review' are excluded
- The _ttg_candidate_to_feed_entry conversion is correct
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

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


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTTGCandidateConversion:
    def test_entry_has_required_fields(self):
        from run_intelligence_pipeline import _ttg_candidate_to_feed_entry
        ttg = {
            "symbol": "NVDA",
            "theme_id": "ai_compute",
            "bucket_id": "gpu_hardware",
            "exposure_type": "direct",
            "confidence": 0.85,
            "reason_to_care": "NVDA is core AI compute",
            "route_hint": "swing",
            "status": "active",
            "driver_active": True,
        }
        entry = _ttg_candidate_to_feed_entry(ttg, "2026-06-02T10:00:00Z")
        assert entry["symbol"] == "NVDA"
        assert entry["candidate_source"] == "theme_transmission_graph"
        assert "theme_transmission_graph" in entry["source_labels"]
        assert entry["role"] == "direct_beneficiary"
        assert entry["confidence"] == 0.85
        assert entry["route_hint"] == ["swing"]
        assert entry["bucket_id"] == "gpu_hardware"

    def test_route_hint_string_becomes_list(self):
        from run_intelligence_pipeline import _ttg_candidate_to_feed_entry
        ttg = {"symbol": "AMD", "route_hint": "position", "confidence": 0.7}
        entry = _ttg_candidate_to_feed_entry(ttg, "2026-06-02T10:00:00Z")
        assert isinstance(entry["route_hint"], list)
        assert entry["route_hint"] == ["position"]

    def test_route_hint_list_preserved(self):
        from run_intelligence_pipeline import _ttg_candidate_to_feed_entry
        ttg = {"symbol": "MSFT", "route_hint": ["swing", "watchlist"], "confidence": 0.75}
        entry = _ttg_candidate_to_feed_entry(ttg, "2026-06-02T10:00:00Z")
        assert entry["route_hint"] == ["swing", "watchlist"]

    def test_default_confidence_when_missing(self):
        from run_intelligence_pipeline import _ttg_candidate_to_feed_entry
        ttg = {"symbol": "X"}
        entry = _ttg_candidate_to_feed_entry(ttg, "2026-06-02T10:00:00Z")
        assert entry["confidence"] == 0.70

    def test_driver_active_included(self):
        from run_intelligence_pipeline import _ttg_candidate_to_feed_entry
        ttg = {"symbol": "SMCI", "driver_active": True}
        entry = _ttg_candidate_to_feed_entry(ttg, "2026-06-02T10:00:00Z")
        assert entry["driver_active"] is True


class TestInjectTTGIntoFeed:
    def test_ttg_candidates_appear_in_feed(self):
        from run_intelligence_pipeline import _inject_ttg_into_feed
        import sys, types

        ttg_candidates = [
            {"symbol": "NVDA", "status": "active", "confidence": 0.85,
             "theme_id": "ai", "bucket_id": "gpu", "route_hint": "swing",
             "reason_to_care": "AI compute", "driver_active": True},
            {"symbol": "AMD", "status": "active", "confidence": 0.75,
             "theme_id": "ai", "bucket_id": "gpu", "route_hint": "swing",
             "reason_to_care": "AMD GPU", "driver_active": False},
        ]

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            feed_path = f.name
        try:
            _write_feed(feed_path, [])
            fake_tg = types.ModuleType("theme_graph")
            fake_tg.get_shadow_candidates = lambda: ttg_candidates
            sys.modules["theme_graph"] = fake_tg
            try:
                count = _inject_ttg_into_feed(feed_path)
            finally:
                del sys.modules["theme_graph"]

            assert count == 2
            candidates = _read_feed_candidates(feed_path)
            syms = {c["symbol"] for c in candidates}
            assert "NVDA" in syms
            assert "AMD" in syms
        finally:
            os.unlink(feed_path)

    def test_dedup_intelligence_layer_wins(self):
        """Symbols already in the feed from intelligence layer are NOT overwritten."""
        from run_intelligence_pipeline import _inject_ttg_into_feed
        import sys, types

        existing = [{"symbol": "NVDA", "role": "direct_beneficiary",
                     "source_labels": ["economic_intelligence"],
                     "confidence": 0.92, "reason_to_care": "already here"}]
        ttg_candidates = [
            {"symbol": "NVDA", "status": "active", "confidence": 0.80,
             "theme_id": "ai", "bucket_id": "gpu", "route_hint": "swing",
             "reason_to_care": "TTG version"},
        ]

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            feed_path = f.name
        try:
            _write_feed(feed_path, existing)
            fake_tg = types.ModuleType("theme_graph")
            fake_tg.get_shadow_candidates = lambda: ttg_candidates
            sys.modules["theme_graph"] = fake_tg
            try:
                count = _inject_ttg_into_feed(feed_path)
            finally:
                del sys.modules["theme_graph"]

            # NVDA already in feed — should not be re-added
            assert count == 0
            candidates = _read_feed_candidates(feed_path)
            nvda = [c for c in candidates if c["symbol"] == "NVDA"]
            assert len(nvda) == 1
            # Intelligence layer version preserved (confidence = 0.92)
            assert nvda[0]["confidence"] == 0.92
        finally:
            os.unlink(feed_path)

    def test_needs_review_excluded(self):
        """Symbols with status='needs_review' must not enter the feed."""
        from run_intelligence_pipeline import _inject_ttg_into_feed
        import sys, types

        ttg_candidates = [
            {"symbol": "XYZ", "status": "needs_review", "confidence": 0.80,
             "theme_id": "ai", "bucket_id": "gpu", "route_hint": "swing",
             "reason_to_care": "needs review"},
            {"symbol": "ANET", "status": "active", "confidence": 0.78,
             "theme_id": "ai", "bucket_id": "network", "route_hint": "swing",
             "reason_to_care": "Active candidate"},
        ]

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            feed_path = f.name
        try:
            _write_feed(feed_path, [])
            fake_tg = types.ModuleType("theme_graph")
            fake_tg.get_shadow_candidates = lambda: ttg_candidates
            sys.modules["theme_graph"] = fake_tg
            try:
                count = _inject_ttg_into_feed(feed_path)
            finally:
                del sys.modules["theme_graph"]

            assert count == 1
            candidates = _read_feed_candidates(feed_path)
            syms = {c["symbol"] for c in candidates}
            assert "XYZ" not in syms
            assert "ANET" in syms
        finally:
            os.unlink(feed_path)

    def test_graceful_on_missing_feed(self):
        """If the feed file doesn't exist, inject returns 0 and doesn't crash."""
        from run_intelligence_pipeline import _inject_ttg_into_feed
        import sys, types

        ttg_candidates = [{"symbol": "NVDA", "status": "active", "confidence": 0.8}]
        fake_tg = types.ModuleType("theme_graph")
        fake_tg.get_shadow_candidates = lambda: ttg_candidates
        sys.modules["theme_graph"] = fake_tg
        try:
            count = _inject_ttg_into_feed("/tmp/does_not_exist_xxxxxx.json")
        finally:
            del sys.modules["theme_graph"]
        assert count == 0

    def test_graceful_on_import_error(self):
        """If theme_graph can't be imported, inject returns 0."""
        from run_intelligence_pipeline import _inject_ttg_into_feed
        import sys

        # Ensure theme_graph raises ImportError
        if "theme_graph" in sys.modules:
            saved = sys.modules.pop("theme_graph")
        else:
            saved = None

        import builtins
        real_import = builtins.__import__
        def _broken_import(name, *args, **kwargs):
            if name == "theme_graph":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)
        builtins.__import__ = _broken_import
        try:
            count = _inject_ttg_into_feed("/tmp/irrelevant.json")
        finally:
            builtins.__import__ = real_import
            if saved is not None:
                sys.modules["theme_graph"] = saved
        assert count == 0


class TestTTGCandidateSourceLabel:
    def test_source_label_is_ttg(self):
        from run_intelligence_pipeline import _ttg_candidate_to_feed_entry
        ttg = {"symbol": "CRDO", "confidence": 0.75, "status": "active"}
        entry = _ttg_candidate_to_feed_entry(ttg, "2026-06-02T10:00:00Z")
        assert "theme_transmission_graph" in entry["source_labels"]
        assert entry["candidate_source"] == "theme_transmission_graph"
