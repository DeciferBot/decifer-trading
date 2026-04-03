"""
Tests for feature_sequencer.py — observation window enforcement.

Coverage:
  TestCanActivate          — window blocking and pass-through logic
  TestRecordActivation     — persistence and idempotency
  TestGetStatus            — status dict structure
  TestAnalyticsOnlyExempt  — analytics-only features bypass constraint
  TestBackfillIntegrity    — pre-populated activation log is consistent
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# We need to redirect ACTIVATION_LOG_FILE before importing the module
# so tests never touch the real data directory.
import importlib
import sys


def _make_sequencer(tmp_dir: str):
    """Import feature_sequencer with ACTIVATION_LOG_FILE patched to tmp_dir."""
    log_file = os.path.join(tmp_dir, "feature_activation_log.json")
    # Re-import fresh copy so module-level path is patched correctly
    if "feature_sequencer" in sys.modules:
        del sys.modules["feature_sequencer"]
    import feature_sequencer as fs
    fs.ACTIVATION_LOG_FILE = log_file
    return fs


class TestCanActivate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fs = _make_sequencer(self.tmp)

    # ── No prior activations ────────────────────────────────────

    def test_first_activation_always_allowed(self):
        ok, reason = self.fs.can_activate("feat-ic-weighted-scoring")
        self.assertTrue(ok)
        self.assertIn("first activation", reason)

    def test_analytics_only_always_allowed_even_without_log(self):
        ok, reason = self.fs.can_activate("feat-alpha-decay")
        self.assertTrue(ok)
        self.assertIn("analytics-only", reason)

    # ── Within observation window ───────────────────────────────

    def test_blocked_when_within_window(self):
        six_days_ago = datetime.now(timezone.utc) - timedelta(days=6)
        self.fs.record_activation(
            "feat-ic-weighted-scoring", _now=six_days_ago
        )
        ok, reason = self.fs.can_activate("feat-alpha-pipeline-v2")
        self.assertFalse(ok)
        self.assertIn("Observation window not elapsed", reason)
        self.assertIn("feat-ic-weighted-scoring", reason)

    def test_blocked_reason_includes_remaining_days(self):
        five_days_ago = datetime.now(timezone.utc) - timedelta(days=5)
        self.fs.record_activation("feat-regime-router", _now=five_days_ago)
        ok, reason = self.fs.can_activate("feat-vix-kelly")
        self.assertFalse(ok)
        # Should say ~2 days remaining (5 elapsed, 7 required)
        self.assertIn("2", reason)

    def test_blocked_at_exactly_window_minus_one_second(self):
        almost = datetime.now(timezone.utc) - timedelta(
            days=self.fs.OBSERVATION_WINDOW_DAYS, seconds=-1
        )
        self.fs.record_activation("feat-ic-weighted-scoring", _now=almost)
        ok, _ = self.fs.can_activate("feat-regime-router")
        self.assertFalse(ok)

    # ── After observation window elapsed ────────────────────────

    def test_allowed_after_window_elapsed(self):
        eight_days_ago = datetime.now(timezone.utc) - timedelta(days=8)
        self.fs.record_activation(
            "feat-ic-weighted-scoring", _now=eight_days_ago
        )
        ok, reason = self.fs.can_activate("feat-alpha-pipeline-v2")
        self.assertTrue(ok)
        self.assertIn("elapsed", reason)

    def test_allowed_at_exactly_window_boundary(self):
        exactly = datetime.now(timezone.utc) - timedelta(
            days=self.fs.OBSERVATION_WINDOW_DAYS
        )
        self.fs.record_activation("feat-regime-router", _now=exactly)
        ok, _ = self.fs.can_activate("feat-vix-kelly")
        self.assertTrue(ok)

    # ── Re-activation of same feature ──────────────────────────

    def test_reactivating_same_feature_is_allowed(self):
        one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
        self.fs.record_activation("feat-ic-weighted-scoring", _now=one_day_ago)
        ok, reason = self.fs.can_activate("feat-ic-weighted-scoring")
        self.assertTrue(ok)
        self.assertIn("already recorded", reason)

    # ── Custom window override ──────────────────────────────────

    def test_custom_window_override(self):
        three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
        self.fs.record_activation("feat-ic-weighted-scoring", _now=three_days_ago)
        # Default 7-day window → blocked
        ok, _ = self.fs.can_activate("feat-regime-router")
        self.assertFalse(ok)
        # Custom 2-day window → allowed
        ok, _ = self.fs.can_activate(
            "feat-regime-router", observation_window_days=2
        )
        self.assertTrue(ok)

    # ── Unknown feature (not in either set) ────────────────────

    def test_unknown_feature_is_allowed(self):
        ok, reason = self.fs.can_activate("feat-some-new-thing")
        self.assertTrue(ok)
        self.assertIn("not in SIZING_AFFECTING_FEATURES", reason)


class TestRecordActivation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fs = _make_sequencer(self.tmp)

    def test_record_creates_log_file(self):
        self.assertFalse(os.path.exists(self.fs.ACTIVATION_LOG_FILE))
        self.fs.record_activation("feat-ic-weighted-scoring")
        self.assertTrue(os.path.exists(self.fs.ACTIVATION_LOG_FILE))

    def test_record_stores_correct_fields(self):
        now = datetime(2026, 3, 30, 12, 0, 0, tzinfo=timezone.utc)
        record = self.fs.record_activation(
            "feat-ic-weighted-scoring", approved_by="Amit",
            notes="Phase 1 shipped", _now=now
        )
        self.assertEqual(record["feature_id"], "feat-ic-weighted-scoring")
        self.assertEqual(record["approved_by"], "Amit")
        self.assertEqual(record["notes"], "Phase 1 shipped")
        self.assertTrue(record["is_sizing_affecting"])
        self.assertIn("2026-03-30", record["activated_at"])

    def test_record_persists_to_disk(self):
        self.fs.record_activation("feat-ic-weighted-scoring")
        with open(self.fs.ACTIVATION_LOG_FILE) as f:
            data = json.load(f)
        self.assertEqual(len(data["activations"]), 1)
        self.assertEqual(data["activations"][0]["feature_id"], "feat-ic-weighted-scoring")

    def test_multiple_records_accumulate(self):
        d1 = datetime(2026, 3, 29, tzinfo=timezone.utc)
        d2 = datetime(2026, 4, 5, tzinfo=timezone.utc)
        self.fs.record_activation("feat-vix-kelly", _now=d1)
        self.fs.record_activation("feat-ic-weighted-scoring", _now=d2)
        with open(self.fs.ACTIVATION_LOG_FILE) as f:
            data = json.load(f)
        self.assertEqual(len(data["activations"]), 2)

    def test_analytics_only_feature_marked_correctly(self):
        record = self.fs.record_activation("feat-alpha-decay")
        self.assertFalse(record["is_sizing_affecting"])

    def test_get_last_activation_returns_most_recent(self):
        d1 = datetime(2026, 3, 30, tzinfo=timezone.utc)
        d2 = datetime(2026, 4, 1, tzinfo=timezone.utc)
        self.fs.record_activation("feat-ic-weighted-scoring", _now=d1)
        self.fs.record_activation("feat-alpha-pipeline-v2", _now=d2)
        last = self.fs.get_last_sizing_activation()
        self.assertEqual(last["feature_id"], "feat-alpha-pipeline-v2")

    def test_get_last_activation_ignores_analytics_only(self):
        d1 = datetime(2026, 3, 30, tzinfo=timezone.utc)
        d2 = datetime(2026, 4, 5, tzinfo=timezone.utc)
        self.fs.record_activation("feat-ic-weighted-scoring", _now=d1)
        self.fs.record_activation("feat-alpha-decay", _now=d2)
        last = self.fs.get_last_sizing_activation()
        # analytics-only feature should not become the "last sizing activation"
        self.assertEqual(last["feature_id"], "feat-ic-weighted-scoring")


class TestGetStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fs = _make_sequencer(self.tmp)

    def test_status_when_no_activations(self):
        status = self.fs.get_status()
        self.assertIsNone(status["last_sizing_activation"])
        self.assertIsNone(status["days_since_last_activation"])
        self.assertTrue(status["window_elapsed"])
        self.assertEqual(status["total_activations_recorded"], 0)
        self.assertEqual(status["observation_window_days"], self.fs.OBSERVATION_WINDOW_DAYS)

    def test_status_within_window(self):
        three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
        self.fs.record_activation("feat-ic-weighted-scoring", _now=three_days_ago)
        status = self.fs.get_status()
        self.assertFalse(status["window_elapsed"])
        self.assertAlmostEqual(status["days_since_last_activation"], 3.0, delta=0.1)

    def test_status_after_window_elapsed(self):
        ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
        self.fs.record_activation("feat-ic-weighted-scoring", _now=ten_days_ago)
        status = self.fs.get_status()
        self.assertTrue(status["window_elapsed"])

    def test_status_includes_feature_sets(self):
        status = self.fs.get_status()
        self.assertIn("feat-ic-weighted-scoring", status["sizing_affecting_features"])
        self.assertIn("feat-alpha-decay", status["analytics_only_features"])

    def test_status_total_activations_counts_all_types(self):
        self.fs.record_activation("feat-ic-weighted-scoring")
        self.fs.record_activation("feat-alpha-decay")  # analytics-only
        status = self.fs.get_status()
        # Both are counted in total_activations_recorded
        self.assertEqual(status["total_activations_recorded"], 2)


class TestAnalyticsOnlyExempt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fs = _make_sequencer(self.tmp)

    def test_feat_alpha_decay_never_blocked(self):
        # Even if sizing feature was just activated 1 minute ago
        one_minute_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.fs.record_activation("feat-alpha-pipeline-v2", _now=one_minute_ago)
        ok, reason = self.fs.can_activate("feat-alpha-decay")
        self.assertTrue(ok)
        self.assertIn("analytics-only", reason)

    def test_feat_dim_flags_never_blocked(self):
        one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
        self.fs.record_activation("feat-regime-router", _now=one_day_ago)
        ok, reason = self.fs.can_activate("feat-dim-flags")
        self.assertTrue(ok)

    def test_analytics_only_activation_does_not_start_clock(self):
        # Record analytics-only, then check sizing feature is unblocked
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        self.fs.record_activation("feat-alpha-decay", _now=yesterday)
        # No sizing activation on record — sizing feature should be allowed
        ok, reason = self.fs.can_activate("feat-ic-weighted-scoring")
        self.assertTrue(ok)
        self.assertIn("No prior sizing-affecting activation", reason)


class TestBackfillIntegrity(unittest.TestCase):
    """Verify the pre-populated activation log is internally consistent."""

    LOG_FILE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "feature_activation_log.json"
    )

    def test_log_file_exists(self):
        self.assertTrue(
            os.path.exists(self.LOG_FILE),
            f"Activation log missing: {self.LOG_FILE}"
        )

    def test_log_file_is_valid_json(self):
        with open(self.LOG_FILE) as f:
            data = json.load(f)
        self.assertIn("activations", data)
        self.assertIsInstance(data["activations"], list)

    def test_all_records_have_required_fields(self):
        with open(self.LOG_FILE) as f:
            data = json.load(f)
        for rec in data["activations"]:
            self.assertIn("feature_id", rec, f"Missing feature_id in {rec}")
            self.assertIn("activated_at", rec, f"Missing activated_at in {rec}")
            self.assertIn("approved_by", rec, f"Missing approved_by in {rec}")
            self.assertIn("is_sizing_affecting", rec, f"Missing is_sizing_affecting in {rec}")

    def test_activated_at_is_parseable_iso8601(self):
        with open(self.LOG_FILE) as f:
            data = json.load(f)
        for rec in data["activations"]:
            ts = datetime.fromisoformat(rec["activated_at"])
            self.assertIsNotNone(ts)

    def test_alpha_pipeline_v2_is_last_sizing_activation(self):
        """feat-alpha-pipeline-v2 (2026-04-01) should be the most recent sizing activation."""
        with open(self.LOG_FILE) as f:
            data = json.load(f)
        import feature_sequencer as fs
        sizing = [a for a in data["activations"] if a["feature_id"] in fs.SIZING_AFFECTING_FEATURES]
        last = max(sizing, key=lambda a: a["activated_at"])
        self.assertEqual(last["feature_id"], "feat-alpha-pipeline-v2")

    def test_three_sizing_features_recorded(self):
        """Backfill should have at least 3 sizing-affecting activations."""
        with open(self.LOG_FILE) as f:
            data = json.load(f)
        import feature_sequencer as fs
        sizing = [a for a in data["activations"] if a["feature_id"] in fs.SIZING_AFFECTING_FEATURES]
        self.assertGreaterEqual(len(sizing), 3)

    def test_regime_router_and_ic_shipped_same_day(self):
        """
        The root cause: feat-regime-router and feat-ic-weighted-scoring both
        activated on 2026-03-30. This test documents the violation so it's
        visible in the test suite.
        """
        with open(self.LOG_FILE) as f:
            data = json.load(f)
        dates = {
            a["feature_id"]: a["activated_at"][:10]
            for a in data["activations"]
        }
        self.assertEqual(
            dates.get("feat-ic-weighted-scoring"),
            dates.get("feat-regime-router"),
            "Root-cause violation: both features shipped same day (2026-03-30)"
        )


if __name__ == "__main__":
    unittest.main()
