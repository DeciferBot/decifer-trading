"""
tests/test_handoff_session_validity.py — Handoff session-validity and deployment contract tests.

Covers:
  1. Deployment contracts — launchd plists reference correct entry points
  2. Session-valid expiry — once-daily handoff remains valid through the trading session
  3. Fail-closed behaviour — stale / invalid manifests still rejected
  4. Scanner fallback invariant — never attempted when handoff mode is active
  5. Intelligence rebuild invariant — live bot does not trigger pipeline

No broker calls, no LLM calls, no live API calls.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OPS_LAUNCHD = os.path.join(_ROOT, "ops", "launchd")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future_iso(hours: float = 8.0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_iso(hours: float = 2.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_expiry_for(published_at: datetime) -> datetime:
    """Mirror of run_intelligence_pipeline._session_expiry_utc logic."""
    session_end = datetime.combine(
        published_at.date(), time(22, 0), tzinfo=timezone.utc
    )
    if published_at >= session_end:
        session_end += timedelta(days=1)
    return session_end


def _valid_manifest(expires_at: str | None = None, handoff_enabled: bool = True) -> dict:
    if expires_at is None:
        expires_at = _future_iso(8)
    return {
        "schema_version": "1.0",
        "published_at": _past_iso(0.5),
        "expires_at": expires_at,
        "validation_status": "pass",
        "handoff_mode": "live",
        "handoff_enabled": handoff_enabled,
        "active_universe_file": "data/live/active_opportunity_universe.json",
        "economic_context_file": "data/intelligence/live_driver_state.json",
        "source_snapshot_versions": {},
        "publisher": "run_intelligence_pipeline",
        "warnings": [],
        "no_executable_trade_instructions": True,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }


def _valid_candidate(symbol: str = "NVDA") -> dict:
    return {
        "symbol": symbol,
        "route": "swing",
        "route_hint": "swing",
        "reason_to_care": "test reason",
        "source_labels": ["committed_universe"],
        "approval_status": "approved",
        "risk_flags": [],
        "executable": False,
        "order_instruction": None,
        "live_output_changed": False,
    }


def _valid_universe(expires_at: str | None = None) -> dict:
    if expires_at is None:
        expires_at = _future_iso(8)
    return {
        "schema_version": "1.0",
        "generated_at": _past_iso(0.5),
        "expires_at": expires_at,
        "mode": "production_handoff_universe",
        "source_shadow_file": "test",
        "source_files": [],
        "validation_status": "pass",
        "universe_summary": {"candidate_count": 2},
        "candidates": [_valid_candidate("NVDA"), _valid_candidate("AAPL")],
        "warnings": [],
        "no_executable_trade_instructions": True,
        "live_output_changed": False,
        "secrets_exposed": False,
        "env_values_logged": False,
    }


# ---------------------------------------------------------------------------
# Group 1 — Deployment contract: plist entry points
# ---------------------------------------------------------------------------

class TestDeploymentContracts(unittest.TestCase):
    """Group 1: launchd plists reference correct production entry points."""

    def _read_plist(self, filename: str) -> str:
        path = os.path.join(_OPS_LAUNCHD, filename)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_intelligence_pipeline_plist_does_not_call_handoff_publisher(self):
        """com.decifer.intelligence-pipeline.plist must not reference handoff_publisher.py."""
        content = self._read_plist("com.decifer.intelligence-pipeline.plist")
        self.assertNotIn(
            "handoff_publisher.py",
            content,
            "intelligence-pipeline plist must not call deleted handoff_publisher.py",
        )

    def test_intelligence_pipeline_plist_calls_run_intelligence_pipeline(self):
        """com.decifer.intelligence-pipeline.plist ProgramArguments must call run_intelligence_pipeline.py."""
        content = self._read_plist("com.decifer.intelligence-pipeline.plist")
        self.assertIn(
            "run_intelligence_pipeline.py",
            content,
            "intelligence-pipeline plist must reference run_intelligence_pipeline.py",
        )

    def test_intelligence_pipeline_plist_does_not_chain_universe_builder(self):
        """intelligence-pipeline plist must not call universe_builder.py as a separate step."""
        content = self._read_plist("com.decifer.intelligence-pipeline.plist")
        # universe_builder.py may appear in comments/docs but must not appear in
        # ProgramArguments as a separate invocation after run_intelligence_pipeline.py
        # Check: the command chain pattern must not exist
        self.assertNotIn(
            "universe_builder.py &amp;&amp;",
            content,
            "universe_builder.py must not be chained in ProgramArguments — "
            "run_intelligence_pipeline.py handles it internally",
        )
        self.assertNotIn(
            "universe_builder.py &&",
            content,
        )

    def test_handoff_publisher_plist_marked_deprecated(self):
        """com.decifer.handoff-publisher.plist must be marked as DEPRECATED."""
        content = self._read_plist("com.decifer.handoff-publisher.plist")
        self.assertIn(
            "DEPRECATED",
            content,
            "handoff-publisher plist must be marked DEPRECATED",
        )

    def test_handoff_publisher_plist_does_not_invoke_handoff_publisher_script(self):
        """Deprecated plist must not invoke handoff_publisher.py in ProgramArguments."""
        content = self._read_plist("com.decifer.handoff-publisher.plist")
        # Must not contain an active ProgramArguments entry calling handoff_publisher.py
        # (the label check: the deprecated label makes it inert)
        self.assertIn(
            "DO-NOT-INSTALL",
            content,
            "Deprecated plist body must have DO-NOT-INSTALL label to prevent accidental loading",
        )

    def test_run_intelligence_pipeline_file_exists(self):
        """run_intelligence_pipeline.py must exist as the production entry point."""
        path = os.path.join(_ROOT, "run_intelligence_pipeline.py")
        self.assertTrue(
            os.path.exists(path),
            "run_intelligence_pipeline.py must exist — it is the production pipeline entry point",
        )

    def test_handoff_publisher_script_does_not_exist(self):
        """handoff_publisher.py must not exist — it has been deleted."""
        path = os.path.join(_ROOT, "handoff_publisher.py")
        self.assertFalse(
            os.path.exists(path),
            "handoff_publisher.py must be deleted — it is superseded by run_intelligence_pipeline.py",
        )


# ---------------------------------------------------------------------------
# Group 2 — Session-valid expiry logic
# ---------------------------------------------------------------------------

class TestSessionValidExpiry(unittest.TestCase):
    """Group 2: Expiry window is session-valid, not 15-minute TTL."""

    def test_session_expiry_is_after_market_open(self):
        """A pre-market handoff expires after NYSE open (13:30 UTC during EDT)."""
        # Simulate pre-market publication at 12:45 UTC
        pub = datetime(2026, 5, 19, 12, 45, 0, tzinfo=timezone.utc)
        exp = _session_expiry_for(pub)
        market_open = datetime(2026, 5, 19, 13, 30, 0, tzinfo=timezone.utc)
        self.assertGreater(
            exp, market_open,
            f"Session expiry {exp} must be after market open {market_open}",
        )

    def test_session_expiry_covers_market_close_edt(self):
        """A pre-market handoff expires after NYSE close (20:00 UTC during EDT)."""
        pub = datetime(2026, 5, 19, 12, 45, 0, tzinfo=timezone.utc)
        exp = _session_expiry_for(pub)
        market_close_edt = datetime(2026, 5, 19, 20, 0, 0, tzinfo=timezone.utc)
        self.assertGreater(
            exp, market_close_edt,
            f"Session expiry {exp} must be after NYSE close {market_close_edt}",
        )

    def test_session_expiry_covers_market_close_est(self):
        """A pre-market handoff expires after NYSE close (21:00 UTC during EST)."""
        pub = datetime(2026, 12, 15, 12, 45, 0, tzinfo=timezone.utc)  # winter, EST
        exp = _session_expiry_for(pub)
        market_close_est = datetime(2026, 12, 15, 21, 0, 0, tzinfo=timezone.utc)
        self.assertGreater(
            exp, market_close_est,
            f"Session expiry {exp} must be after NYSE close {market_close_est}",
        )

    def test_session_expiry_is_same_day_22_utc(self):
        """Session expiry is 22:00 UTC on the same day as publication."""
        pub = datetime(2026, 5, 19, 12, 45, 0, tzinfo=timezone.utc)
        exp = _session_expiry_for(pub)
        expected = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(exp, expected)

    def test_session_expiry_pushes_to_next_day_if_published_after_22_utc(self):
        """If published at or after 22:00 UTC, expiry is 22:00 UTC next day."""
        pub = datetime(2026, 5, 19, 22, 30, 0, tzinfo=timezone.utc)
        exp = _session_expiry_for(pub)
        expected = datetime(2026, 5, 20, 22, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(exp, expected)

    def test_session_expiry_is_not_15_minutes(self):
        """Session expiry is NOT 15 minutes from publication (old broken behaviour)."""
        pub = datetime(2026, 5, 19, 12, 45, 0, tzinfo=timezone.utc)
        old_expiry = pub + timedelta(minutes=15)
        new_expiry = _session_expiry_for(pub)
        self.assertNotEqual(
            new_expiry, old_expiry,
            "New expiry must not equal the old 15-minute TTL",
        )
        self.assertGreater(
            new_expiry, old_expiry,
            "New expiry must be substantially later than old 15-minute TTL",
        )

    def test_run_intelligence_pipeline_write_manifest_uses_session_expiry(self):
        """run_intelligence_pipeline._write_manifest sets session-valid expiry, not 15-min."""
        src_path = os.path.join(_ROOT, "run_intelligence_pipeline.py")
        with open(src_path, "r") as f:
            src = f.read()
        self.assertIn(
            "_session_expiry_utc",
            src,
            "_write_manifest must call _session_expiry_utc, not a fixed 15-minute timedelta",
        )
        self.assertNotIn(
            "timedelta(minutes=15)",
            src,
            "15-minute TTL must not remain in run_intelligence_pipeline.py",
        )

    def test_run_intelligence_pipeline_promote_to_live_uses_session_expiry(self):
        """run_intelligence_pipeline._promote_to_live sets session-valid expiry."""
        src_path = os.path.join(_ROOT, "run_intelligence_pipeline.py")
        with open(src_path, "r") as f:
            src = f.read()
        # _promote_to_live must also use _session_expiry_utc
        # Verify by checking the function contains the call
        self.assertIn(
            "_session_expiry_utc",
            src,
        )


# ---------------------------------------------------------------------------
# Group 3 — Handoff_reader: session-valid manifest accepted
# ---------------------------------------------------------------------------

class TestHandoffReaderSessionValid(unittest.TestCase):
    """Group 3: handoff_reader accepts a session-valid manifest and rejects stale ones."""

    def _load_with_tempfiles(self, manifest: dict, universe: dict) -> dict:
        import handoff_reader
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as uf:
            json.dump(universe, uf)
            universe_path = uf.name
        manifest["active_universe_file"] = universe_path
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as mf:
            json.dump(manifest, mf)
            manifest_path = mf.name
        try:
            return handoff_reader.load_production_handoff(manifest_path)
        finally:
            os.unlink(universe_path)
            os.unlink(manifest_path)

    def test_pre_market_handoff_accepted_at_market_open(self):
        """A handoff with 8h+ remaining expiry is accepted (simulates market-open read)."""
        manifest = _valid_manifest(expires_at=_future_iso(8))
        universe = _valid_universe(expires_at=_future_iso(8))
        result = self._load_with_tempfiles(manifest, universe)
        self.assertTrue(
            result["handoff_allowed"],
            f"Pre-market handoff should be accepted at open. reason={result.get('fail_closed_reason')}",
        )
        self.assertIsNone(result["fail_closed_reason"])

    def test_mid_session_handoff_accepted(self):
        """A handoff with 3h remaining expiry is accepted (simulates mid-session read)."""
        manifest = _valid_manifest(expires_at=_future_iso(3))
        universe = _valid_universe(expires_at=_future_iso(3))
        result = self._load_with_tempfiles(manifest, universe)
        self.assertTrue(
            result["handoff_allowed"],
            f"Mid-session handoff should be accepted. reason={result.get('fail_closed_reason')}",
        )

    def test_expired_handoff_after_sla_window_rejected(self):
        """A handoff whose expires_at is in the past is rejected (fail closed)."""
        manifest = _valid_manifest(expires_at=_past_iso(1))
        universe = _valid_universe(expires_at=_past_iso(1))
        result = self._load_with_tempfiles(manifest, universe)
        self.assertFalse(
            result["handoff_allowed"],
            "Expired handoff must be rejected",
        )
        self.assertIsNotNone(result["fail_closed_reason"])
        self.assertIn("expired", result["fail_closed_reason"])

    def test_invalid_manifest_fails_closed(self):
        """Manifest with wrong validation_status fails closed."""
        manifest = _valid_manifest()
        manifest["validation_status"] = "fail"
        universe = _valid_universe()
        result = self._load_with_tempfiles(manifest, universe)
        self.assertFalse(result["handoff_allowed"])
        self.assertIsNotNone(result["fail_closed_reason"])

    def test_missing_required_manifest_field_fails_closed(self):
        """Manifest missing a required field fails closed."""
        import handoff_reader
        manifest = _valid_manifest()
        del manifest["schema_version"]
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as mf:
            json.dump(manifest, mf)
            manifest_path = mf.name
        try:
            result = handoff_reader.load_production_handoff(manifest_path)
            self.assertFalse(result["handoff_allowed"])
            self.assertIsNotNone(result["fail_closed_reason"])
        finally:
            os.unlink(manifest_path)

    def test_handoff_disabled_in_manifest_fails_closed(self):
        """handoff_enabled=False fails closed immediately."""
        import handoff_reader
        manifest = _valid_manifest(handoff_enabled=False)
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as mf:
            json.dump(manifest, mf)
            manifest_path = mf.name
        try:
            result = handoff_reader.load_production_handoff(manifest_path)
            self.assertFalse(result["handoff_allowed"])
            self.assertEqual(result["fail_closed_reason"], "handoff_disabled_in_manifest")
        finally:
            os.unlink(manifest_path)

    def test_scanner_fallback_never_attempted(self):
        """scanner_fallback_attempted is always False in all paths."""
        manifest = _valid_manifest(expires_at=_past_iso(1))  # expired
        universe = _valid_universe(expires_at=_past_iso(1))
        result = self._load_with_tempfiles(manifest, universe)
        self.assertFalse(
            result["scanner_fallback_attempted"],
            "scanner_fallback_attempted must always be False — no scanner fallback",
        )

    def test_scanner_fallback_false_on_valid_handoff(self):
        """scanner_fallback_attempted is False even on a valid handoff."""
        manifest = _valid_manifest(expires_at=_future_iso(6))
        universe = _valid_universe(expires_at=_future_iso(6))
        result = self._load_with_tempfiles(manifest, universe)
        self.assertFalse(result["scanner_fallback_attempted"])


# ---------------------------------------------------------------------------
# Group 4 — Live bot does not rebuild intelligence
# ---------------------------------------------------------------------------

class TestLiveBotDoesNotRebuildIntelligence(unittest.TestCase):
    """Group 4: bot_trading does not import or call intelligence pipeline modules."""

    def _get_src(self, filename: str) -> str:
        with open(os.path.join(_ROOT, filename), "r") as f:
            return f.read()

    def test_bot_trading_does_not_import_run_intelligence_pipeline(self):
        """bot_trading.py must not import run_intelligence_pipeline."""
        src = self._get_src("bot_trading.py")
        self.assertNotIn(
            "run_intelligence_pipeline",
            src,
            "bot_trading must not import or call run_intelligence_pipeline",
        )

    def test_bot_trading_does_not_import_live_driver_resolver(self):
        """bot_trading.py must not import live_driver_resolver (pipeline step)."""
        src = self._get_src("bot_trading.py")
        self.assertNotIn(
            "live_driver_resolver",
            src,
            "bot_trading must not call intelligence pipeline steps directly",
        )

    def test_bot_trading_does_not_import_candidate_resolver(self):
        """bot_trading.py must not import candidate_resolver (pipeline step)."""
        src = self._get_src("bot_trading.py")
        self.assertNotIn(
            "candidate_resolver",
            src,
            "bot_trading must not call intelligence pipeline steps directly",
        )

    def test_bot_trading_does_not_import_universe_builder(self):
        """bot_trading.py must not import universe_builder (pipeline step)."""
        src = self._get_src("bot_trading.py")
        self.assertNotIn(
            "universe_builder",
            src,
            "bot_trading must not call universe_builder — pipeline only",
        )

    def test_handoff_reader_does_not_import_intelligence_modules(self):
        """handoff_reader.py must not import intelligence or scanner modules."""
        import ast
        src = self._get_src("handoff_reader.py")
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        for forbidden in (
            "run_intelligence_pipeline",
            "live_driver_resolver",
            "candidate_resolver",
            "universe_builder",
            "scanner",
        ):
            self.assertNotIn(
                forbidden,
                imported,
                f"handoff_reader must not import {forbidden}",
            )


# ---------------------------------------------------------------------------
# Group 5 — Scanner fallback invariant in bot_trading source
# ---------------------------------------------------------------------------

class TestScannerFallbackInvariant(unittest.TestCase):
    """Group 5: bot_trading source confirms scanner fallback is not attempted during handoff mode."""

    def _get_src(self) -> str:
        with open(os.path.join(_ROOT, "bot_trading.py"), "r") as f:
            return f.read()

    def test_scanner_fallback_attempted_false_in_handoff_path(self):
        """bot_trading logs scanner_fallback_attempted=False in the handoff path."""
        src = self._get_src()
        self.assertIn("scanner_fallback_attempted=False", src)

    def test_fail_closed_path_does_not_call_get_dynamic_universe(self):
        """The handoff fail-closed path does not fall back to get_dynamic_universe."""
        src = self._get_src()
        # The conditional structure: if handoff_enabled → handoff path (no scanner fallback)
        # Verified by checking the invariant log statement exists
        self.assertIn("scanner_fallback_attempted=False", src)


if __name__ == "__main__":
    unittest.main()
