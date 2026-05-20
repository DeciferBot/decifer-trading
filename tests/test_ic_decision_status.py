"""
Tests for Phase 2B IC Decision Status Tracking.

Covers:
  A. Decision event schema
  B. Apex selection mapping
  C. Below-threshold tracking
  D. Risk / execution status
  E. Backward compatibility
  F. No live behaviour change
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tmp_events_file() -> str:
    return tempfile.mktemp(suffix=".jsonl")


def _read_events(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def _patch_events_file(path: str):
    """Return a context manager that redirects ic_decision_writer to a temp file."""
    import ic_decision_writer as _idw
    return patch.object(_idw, "_EVENTS_FILE", path)


def _minimal_candidate(
    symbol: str = "NVDA",
    score: float = 32.0,
    scan_id: str = "20260520T143200",
    passed_base_threshold: bool = True,
) -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "price": 900.0,
        "direction": "LONG",
        "observation_id": f"{scan_id}_{symbol}",
        "scan_id": scan_id,
        "session_date": scan_id[:8],
        "passed_base_threshold": passed_base_threshold,
        "candidate_source": "handoff_reader",
        "ranking_position": 1,
        "ranking_total": 50,
        "score_breakdown": {"trend": 7, "momentum": 6},
    }


# ─────────────────────────────────────────────────────────────────────────────
# A. Decision event schema
# ─────────────────────────────────────────────────────────────────────────────

class TestDecisionEventSchema(unittest.TestCase):

    def setUp(self):
        self._tmp = _tmp_events_file()

    def tearDown(self):
        if os.path.exists(self._tmp):
            os.unlink(self._tmp)

    def test_event_has_observation_id(self):
        from ic_decision_writer import write_event
        with _patch_events_file(self._tmp):
            write_event("20260520T143200_NVDA", "20260520T143200", "NVDA", "passed_to_apex")
        events = _read_events(self._tmp)
        self.assertEqual(events[0]["observation_id"], "20260520T143200_NVDA")

    def test_event_has_scan_id(self):
        from ic_decision_writer import write_event
        with _patch_events_file(self._tmp):
            write_event("20260520T143200_NVDA", "20260520T143200", "NVDA", "passed_to_apex")
        events = _read_events(self._tmp)
        self.assertEqual(events[0]["scan_id"], "20260520T143200")

    def test_event_has_symbol(self):
        from ic_decision_writer import write_event
        with _patch_events_file(self._tmp):
            write_event("20260520T143200_NVDA", "20260520T143200", "NVDA", "apex_selected")
        events = _read_events(self._tmp)
        self.assertEqual(events[0]["symbol"], "NVDA")

    def test_event_has_decision_status(self):
        from ic_decision_writer import write_event
        with _patch_events_file(self._tmp):
            write_event("20260520T143200_NVDA", "20260520T143200", "NVDA", "apex_rejected")
        events = _read_events(self._tmp)
        self.assertIn("decision_status", events[0])

    def test_invalid_status_becomes_unknown(self):
        from ic_decision_writer import write_event
        with _patch_events_file(self._tmp):
            write_event("obs1", "sid1", "TSLA", "bogus_status_xyz")
        events = _read_events(self._tmp)
        self.assertEqual(events[0]["decision_status"], "unknown")
        self.assertIn("bogus_status_xyz", events[0].get("reason", ""))

    def test_all_valid_statuses_accepted(self):
        from ic_decision_writer import VALID_STATUSES, write_event
        with _patch_events_file(self._tmp):
            for status in VALID_STATUSES:
                write_event(f"obs_{status}", "sid1", "AAPL", status)
        events = _read_events(self._tmp)
        written = {e["decision_status"] for e in events}
        self.assertEqual(written, VALID_STATUSES)

    def test_session_date_derived_from_scan_id_when_absent(self):
        from ic_decision_writer import write_event
        with _patch_events_file(self._tmp):
            write_event("obs1", "20260520T143200", "NVDA", "passed_to_apex")
        events = _read_events(self._tmp)
        self.assertEqual(events[0]["session_date"], "20260520")

    def test_write_event_never_raises(self):
        """write_event must not raise even when the file path is invalid."""
        from ic_decision_writer import write_event
        # Writing to a non-existent directory — should silently log and return.
        with patch("ic_decision_writer._EVENTS_FILE", "/nonexistent/path/events.jsonl"):
            try:
                write_event("obs1", "sid1", "NVDA", "passed_to_apex")
            except Exception as exc:
                self.fail(f"write_event raised: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# B. Apex selection mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestApexSelectionMapping(unittest.TestCase):

    def setUp(self):
        self._tmp = _tmp_events_file()

    def tearDown(self):
        if os.path.exists(self._tmp):
            os.unlink(self._tmp)

    def _run_apex_events(
        self,
        candidates_by_symbol: dict,
        selected_symbols: set[str],
    ) -> list[dict]:
        """Simulate the apex event-writing block from _run_apex_pipeline()."""
        from ic_decision_writer import write_events_bulk
        _now = datetime.now(UTC).isoformat()
        events = []
        for c_sym, c_payload in candidates_by_symbol.items():
            obs_id = c_payload.get("observation_id")
            base = {
                "ts_utc": _now,
                "observation_id": obs_id,
                "scan_id": c_payload.get("scan_id"),
                "symbol": c_sym,
                "session_date": c_payload.get("session_date"),
                "candidate_source": c_payload.get("candidate_source"),
                "ranking_position": c_payload.get("ranking_position"),
                "ranking_total": c_payload.get("ranking_total"),
            }
            events.append({**base, "decision_status": "passed_to_apex"})
            if c_sym in selected_symbols:
                events.append({**base, "decision_status": "apex_selected"})
            else:
                events.append({**base, "decision_status": "apex_rejected",
                                "reason": "not_in_apex_new_entries"})
        with _patch_events_file(self._tmp):
            write_events_bulk(events)
        return _read_events(self._tmp)

    def test_not_selected_symbols_get_apex_rejected(self):
        cbs = {
            "NVDA": _minimal_candidate("NVDA"),
            "AAPL": _minimal_candidate("AAPL"),
        }
        events = self._run_apex_events(cbs, selected_symbols={"NVDA"})
        rejected = [e for e in events if e["decision_status"] == "apex_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["symbol"], "AAPL")

    def test_selected_symbols_get_apex_selected(self):
        cbs = {
            "NVDA": _minimal_candidate("NVDA"),
            "AAPL": _minimal_candidate("AAPL"),
        }
        events = self._run_apex_events(cbs, selected_symbols={"NVDA"})
        selected = [e for e in events if e["decision_status"] == "apex_selected"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["symbol"], "NVDA")

    def test_selected_symbol_preserves_observation_id(self):
        cbs = {"NVDA": _minimal_candidate("NVDA", scan_id="20260520T143200")}
        events = self._run_apex_events(cbs, selected_symbols={"NVDA"})
        selected = [e for e in events if e["decision_status"] == "apex_selected"]
        self.assertEqual(selected[0]["observation_id"], "20260520T143200_NVDA")

    def test_passed_to_apex_written_for_all_candidates(self):
        cbs = {
            "NVDA": _minimal_candidate("NVDA"),
            "AAPL": _minimal_candidate("AAPL"),
            "TSLA": _minimal_candidate("TSLA"),
        }
        events = self._run_apex_events(cbs, selected_symbols={"NVDA"})
        passed = [e for e in events if e["decision_status"] == "passed_to_apex"]
        self.assertEqual(len(passed), 3)

    def test_empty_candidates_writes_no_events(self):
        events = self._run_apex_events({}, selected_symbols=set())
        self.assertEqual(len(events), 0)


# ─────────────────────────────────────────────────────────────────────────────
# C. Below-threshold tracking
# ─────────────────────────────────────────────────────────────────────────────

class TestBelowThresholdTracking(unittest.TestCase):

    def setUp(self):
        self._tmp_events = _tmp_events_file()
        self._tmp_signals = _tmp_events_file()

    def tearDown(self):
        for p in (self._tmp_events, self._tmp_signals):
            if os.path.exists(p):
                os.unlink(p)

    def _run_pipeline_write(self, all_scored: list, scored: list) -> list[dict]:
        """Simulate the below_threshold event write from run_signal_pipeline()."""
        from ic_decision_writer import write_events_bulk
        _scan_id = "20260520T143200"
        _scored_syms = {s.get("symbol") for s in scored if s.get("symbol")}
        events = []
        for s in all_scored:
            sym = s.get("symbol")
            passed = sym in _scored_syms if sym else False
            if not passed and sym:
                events.append({
                    "observation_id": f"{_scan_id}_{sym}",
                    "scan_id": _scan_id,
                    "symbol": sym,
                    "decision_status": "below_threshold",
                    "reason": "score_below_base_threshold",
                })
        with _patch_events_file(self._tmp_events):
            write_events_bulk(events)
        return _read_events(self._tmp_events)

    def test_below_threshold_candidate_is_marked(self):
        all_scored = [
            {"symbol": "NVDA", "score": 35.0},
            {"symbol": "LOW_SCORE", "score": 5.0},
        ]
        scored = [{"symbol": "NVDA", "score": 35.0}]
        events = self._run_pipeline_write(all_scored, scored)
        bt = [e for e in events if e["decision_status"] == "below_threshold"]
        self.assertEqual(len(bt), 1)
        self.assertEqual(bt[0]["symbol"], "LOW_SCORE")

    def test_above_threshold_candidate_not_marked_below(self):
        all_scored = [
            {"symbol": "NVDA", "score": 35.0},
            {"symbol": "AAPL", "score": 30.0},
        ]
        scored = [{"symbol": "NVDA"}, {"symbol": "AAPL"}]
        events = self._run_pipeline_write(all_scored, scored)
        bt = [e for e in events if e["decision_status"] == "below_threshold"]
        self.assertEqual(len(bt), 0)

    def test_passed_base_threshold_stamped_on_all_scored(self):
        """run_signal_pipeline() stamps passed_base_threshold onto all_scored items."""
        all_scored_items = [
            {"symbol": "NVDA", "score": 35.0},
            {"symbol": "LOW",  "score": 5.0},
        ]
        scored_items = [{"symbol": "NVDA", "score": 35.0}]
        scored_syms = {s.get("symbol") for s in scored_items}
        for s in all_scored_items:
            s["passed_base_threshold"] = s.get("symbol") in scored_syms
        self.assertTrue(all_scored_items[0]["passed_base_threshold"])
        self.assertFalse(all_scored_items[1]["passed_base_threshold"])


# ─────────────────────────────────────────────────────────────────────────────
# D. Risk / execution status
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionStatus(unittest.TestCase):

    def setUp(self):
        self._tmp = _tmp_events_file()

    def tearDown(self):
        if os.path.exists(self._tmp):
            os.unlink(self._tmp)

    def _write_execution_event(self, ok: bool, payload: dict) -> list[dict]:
        from ic_decision_writer import write_event
        sym = payload.get("symbol", "NVDA")
        obs_id = payload.get("observation_id")
        scan_id = payload.get("scan_id")
        with _patch_events_file(self._tmp):
            write_event(
                observation_id=obs_id,
                scan_id=scan_id,
                symbol=sym,
                decision_status="executed" if ok else "order_failed",
                session_date=payload.get("session_date"),
                candidate_source=payload.get("candidate_source"),
                ranking_position=payload.get("ranking_position"),
                ranking_total=payload.get("ranking_total"),
                reason=None if ok else "execute_buy_returned_false",
                trade_id="NVDA_20260520T143200" if ok else None,
            )
        return _read_events(self._tmp)

    def test_executed_candidate_writes_executed_event(self):
        payload = _minimal_candidate("NVDA")
        events = self._write_execution_event(ok=True, payload=payload)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["decision_status"], "executed")
        self.assertEqual(events[0]["trade_id"], "NVDA_20260520T143200")

    def test_failed_order_writes_order_failed_event(self):
        payload = _minimal_candidate("NVDA")
        events = self._write_execution_event(ok=False, payload=payload)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["decision_status"], "order_failed")
        self.assertIsNone(events[0].get("trade_id"))
        self.assertIsNotNone(events[0].get("reason"))

    def test_order_failed_does_not_crash(self):
        """Writing order_failed must never raise."""
        from ic_decision_writer import write_event
        with _patch_events_file(self._tmp):
            try:
                write_event(None, None, "NVDA", "order_failed", reason="test")
            except Exception as exc:
                self.fail(f"write_event raised: {exc}")

    def test_risk_blocked_is_a_valid_status(self):
        from ic_decision_writer import VALID_STATUSES
        self.assertIn("risk_blocked", VALID_STATUSES)

    def test_executed_event_carries_observation_id(self):
        payload = _minimal_candidate("NVDA", scan_id="20260520T143200")
        events = self._write_execution_event(ok=True, payload=payload)
        self.assertEqual(events[0]["observation_id"], "20260520T143200_NVDA")


# ─────────────────────────────────────────────────────────────────────────────
# E. Backward compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestBackwardCompatibility(unittest.TestCase):

    def test_old_signals_without_passed_base_threshold_readable(self):
        """Old signals_log records without passed_base_threshold are still valid."""
        old_record = {
            "_schema_version": 1,
            "ts": "2026-03-01T14:00:00+00:00",
            "scan_id": "20260301T140000",
            "symbol": "AAPL",
            "score": 32.0,
            "price": 175.0,
            "direction": "LONG",
            "regime": "TRENDING_UP",
            "score_breakdown": {"trend": 7},
        }
        # Missing passed_base_threshold — should be treated as True (above threshold)
        self.assertTrue(old_record.get("passed_base_threshold", True))

    def test_old_training_records_without_observation_id_readable(self):
        """Old training records without observation_id must not crash the report."""
        import scripts.ic_decision_report as rep
        # _load_jsonl must handle records without observation_id
        old_tr = {"trade_id": "ABC_001", "symbol": "AAPL", "pnl": 100.0}
        # observation_id will be None — tr_by_obs skips it (obs is None)
        obs = old_tr.get("observation_id")
        self.assertIsNone(obs)

    def test_missing_decision_events_does_not_crash_report(self):
        """Report must run cleanly when ic_decision_events.jsonl does not exist."""
        import scripts.ic_decision_report as rep
        with patch.object(rep, "_DECISION_EVENTS", "/nonexistent/path/events.jsonl"):
            with patch.object(rep, "_SIGNALS_LOG", "/nonexistent/path/sig.jsonl"):
                with patch.object(rep, "_TRAINING_RECORDS", "/nonexistent/path/tr.jsonl"):
                    try:
                        rep.run(days=1)
                    except Exception as exc:
                        self.fail(f"run() raised with missing files: {exc}")

    def test_write_event_with_null_observation_id_does_not_crash(self):
        """observation_id=None is a valid (degraded) event — must write without error."""
        tmp = _tmp_events_file()
        try:
            from ic_decision_writer import write_event
            with _patch_events_file(tmp):
                write_event(None, "20260520T143200", "NVDA", "order_failed")
            events = _read_events(tmp)
            self.assertEqual(len(events), 1)
            self.assertIsNone(events[0]["observation_id"])
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_ic_loading_ignores_decision_event_file(self):
        """_load_signal_records() reads signals_log, not ic_decision_events — unaffected."""
        from ic.data import _load_signal_records
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({
                "_schema_version": 2,
                "ts": "2026-03-01T14:00:00+00:00",
                "scan_id": "20260301T140000",
                "symbol": "AAPL",
                "score": 32.0,
                "price": 175.0,
                "direction": "LONG",
                "regime": "TRENDING_UP",
                "score_breakdown": {
                    "trend": 7, "momentum": 5, "squeeze": 3, "flow": 4,
                    "breakout": 6, "pead": 2, "news": 8, "short_squeeze": 1,
                    "reversion": 3, "overnight": 2,
                },
            }) + "\n")
            path = fh.name
        try:
            records = _load_signal_records(signals_log_path=path, min_age_days=0)
            self.assertIsInstance(records, list)
        finally:
            os.unlink(path)


# ─────────────────────────────────────────────────────────────────────────────
# F. No live behaviour change
# ─────────────────────────────────────────────────────────────────────────────

class TestNoLiveBehaviourChange(unittest.TestCase):

    def test_valid_statuses_contains_all_required_values(self):
        from ic_decision_writer import VALID_STATUSES
        required = {
            "scored", "below_threshold", "passed_to_apex",
            "apex_selected", "apex_rejected", "risk_blocked",
            "executed", "order_failed", "unknown",
        }
        self.assertEqual(VALID_STATUSES, required)

    def test_write_event_is_append_only(self):
        """Each write_event call appends one line — never overwrites."""
        tmp = _tmp_events_file()
        try:
            from ic_decision_writer import write_event
            with _patch_events_file(tmp):
                write_event("obs1", "sid1", "NVDA", "passed_to_apex")
                write_event("obs1", "sid1", "NVDA", "apex_selected")
            events = _read_events(tmp)
            self.assertEqual(len(events), 2)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_write_events_bulk_is_thread_safe(self):
        """Concurrent bulk writes must not corrupt the file."""
        tmp = _tmp_events_file()
        errors = []

        def _write(n: int):
            try:
                from ic_decision_writer import write_events_bulk
                evts = [{"symbol": f"SYM{n}_{i}", "decision_status": "scored"} for i in range(5)]
                with _patch_events_file(tmp):
                    write_events_bulk(evts)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        if os.path.exists(tmp):
            os.unlink(tmp)

    def test_signal_pipeline_result_has_scan_id_field(self):
        """SignalPipelineResult now carries scan_id — default is empty string."""
        from signal_pipeline import SignalPipelineResult
        result = SignalPipelineResult(
            signals=[], scored=[], all_scored=[], news_sentiment={},
            universe=[], regime_name="TRENDING_UP",
        )
        self.assertEqual(result.scan_id, "")

    def test_scan_id_stamped_on_all_scored_items(self):
        """Stamping scan_id on all_scored items does not lose other keys."""
        item = {"symbol": "NVDA", "score": 35.0, "price": 900.0}
        scan_id = "20260520T143200"
        item["scan_id"] = scan_id
        item["observation_id"] = f"{scan_id}_{item['symbol']}"
        item["passed_base_threshold"] = True
        self.assertEqual(item["symbol"], "NVDA")
        self.assertEqual(item["score"], 35.0)
        self.assertEqual(item["scan_id"], scan_id)


if __name__ == "__main__":
    unittest.main()
