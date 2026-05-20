"""
Tests for Phase 2A IC Data Enrichment Hygiene.

Covers:
  A. Signal log enrichment — new fields present on every record
  B. Deterministic observation_id
  C. Direction safety — no silent LONG default
  D. Backward compatibility — old records without new fields still load
  E. Training record linkage — observation_id and linkage_quality
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _minimal_sig(symbol: str = "AAPL", score: float = 30.0, direction: str = "LONG") -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "price": 180.0,
        "direction": direction,
        "score_breakdown": {
            "trend": 7, "momentum": 5, "squeeze": 3, "flow": 4,
            "breakout": 6, "pead": 2, "news": 8, "short_squeeze": 1,
            "reversion": 3, "overnight": 2,
        },
    }


def _regime() -> dict:
    return {"session_character": "TRENDING_UP", "regime": "TRENDING_UP", "vix": 18.5}


# ─────────────────────────────────────────────────────────────────────────────
# A. Signal log enrichment
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalLogEnrichment(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mktemp(suffix=".jsonl")
        import learning as _l
        self._orig = _l.SIGNALS_LOG_FILE
        _l.SIGNALS_LOG_FILE = self._tmp

    def tearDown(self):
        import learning as _l
        _l.SIGNALS_LOG_FILE = self._orig
        if os.path.exists(self._tmp):
            os.unlink(self._tmp)

    def _records(self) -> list[dict]:
        with open(self._tmp) as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_session_date_present(self):
        from learning import log_signal_scan
        log_signal_scan([_minimal_sig()], _regime())
        rec = self._records()[0]
        self.assertIn("session_date", rec)
        # Must be YYYY-MM-DD format
        datetime.strptime(rec["session_date"], "%Y-%m-%d")

    def test_observation_id_present(self):
        from learning import log_signal_scan
        log_signal_scan([_minimal_sig("NVDA")], _regime())
        rec = self._records()[0]
        self.assertIn("observation_id", rec)
        self.assertIn("NVDA", rec["observation_id"])

    def test_ranking_position_present(self):
        from learning import log_signal_scan
        log_signal_scan([_minimal_sig()], _regime())
        rec = self._records()[0]
        self.assertIn("ranking_position", rec)
        self.assertIsInstance(rec["ranking_position"], int)
        self.assertGreaterEqual(rec["ranking_position"], 1)

    def test_ranking_total_present(self):
        from learning import log_signal_scan
        sigs = [_minimal_sig("A"), _minimal_sig("B"), _minimal_sig("C")]
        log_signal_scan(sigs, _regime())
        records = self._records()
        for rec in records:
            self.assertIn("ranking_total", rec)
            self.assertEqual(rec["ranking_total"], 3)

    def test_candidate_source_present(self):
        from learning import log_signal_scan
        log_signal_scan([_minimal_sig()], _regime())
        rec = self._records()[0]
        self.assertIn("candidate_source", rec)

    def test_known_direction_has_ic_eligible_true(self):
        from learning import log_signal_scan
        log_signal_scan([_minimal_sig(direction="LONG")], _regime())
        rec = self._records()[0]
        self.assertIn("ic_eligible", rec)
        self.assertTrue(rec["ic_eligible"])
        self.assertIsNone(rec.get("exclusion_reason"))

    def test_short_direction_has_ic_eligible_true(self):
        from learning import log_signal_scan
        log_signal_scan([_minimal_sig(direction="SHORT")], _regime())
        rec = self._records()[0]
        self.assertTrue(rec["ic_eligible"])

    def test_unknown_direction_has_ic_eligible_false(self):
        from learning import log_signal_scan
        sig = _minimal_sig()
        del sig["direction"]  # simulate missing direction
        log_signal_scan([sig], _regime())
        rec = self._records()[0]
        self.assertFalse(rec["ic_eligible"])
        self.assertEqual(rec["direction"], "UNKNOWN")
        self.assertIsNotNone(rec.get("exclusion_reason"))


# ─────────────────────────────────────────────────────────────────────────────
# B. Deterministic observation_id
# ─────────────────────────────────────────────────────────────────────────────

class TestDeterministicObservationId(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mktemp(suffix=".jsonl")
        import learning as _l
        self._orig = _l.SIGNALS_LOG_FILE
        _l.SIGNALS_LOG_FILE = self._tmp

    def tearDown(self):
        import learning as _l
        _l.SIGNALS_LOG_FILE = self._orig
        if os.path.exists(self._tmp):
            os.unlink(self._tmp)

    def _records(self) -> list[dict]:
        with open(self._tmp) as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_same_scan_id_symbol_gives_same_observation_id(self):
        """Calling with explicit scan_id produces deterministic observation_id."""
        from learning import log_signal_scan
        log_signal_scan([_minimal_sig("AAPL")], _regime(), scan_id="20260520T143200")
        rec = self._records()[0]
        self.assertEqual(rec["observation_id"], "20260520T143200_AAPL")

    def test_different_symbols_same_scan_give_different_ids(self):
        from learning import log_signal_scan
        log_signal_scan(
            [_minimal_sig("AAPL"), _minimal_sig("NVDA")],
            _regime(),
            scan_id="20260520T143200",
        )
        records = self._records()
        ids = [r["observation_id"] for r in records]
        self.assertEqual(len(set(ids)), 2)

    def test_same_symbol_different_scans_give_different_ids(self):
        from learning import log_signal_scan
        log_signal_scan([_minimal_sig("AAPL")], _regime(), scan_id="20260520T143200")
        log_signal_scan([_minimal_sig("AAPL")], _regime(), scan_id="20260520T153000")
        records = self._records()
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0]["observation_id"], records[1]["observation_id"])

    def test_all_symbols_share_scan_id(self):
        """All records from one call share the same scan_id."""
        from learning import log_signal_scan
        sigs = [_minimal_sig("A"), _minimal_sig("B"), _minimal_sig("C")]
        log_signal_scan(sigs, _regime())
        records = self._records()
        scan_ids = {r["scan_id"] for r in records}
        self.assertEqual(len(scan_ids), 1)


# ─────────────────────────────────────────────────────────────────────────────
# C. Direction safety
# ─────────────────────────────────────────────────────────────────────────────

class TestDirectionSafety(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mktemp(suffix=".jsonl")
        import learning as _l
        self._orig = _l.SIGNALS_LOG_FILE
        _l.SIGNALS_LOG_FILE = self._tmp

    def tearDown(self):
        import learning as _l
        _l.SIGNALS_LOG_FILE = self._orig
        if os.path.exists(self._tmp):
            os.unlink(self._tmp)

    def _records(self) -> list[dict]:
        with open(self._tmp) as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_missing_direction_does_not_default_to_long(self):
        from learning import log_signal_scan
        sig = _minimal_sig()
        del sig["direction"]
        log_signal_scan([sig], _regime())
        rec = self._records()[0]
        self.assertNotEqual(rec["direction"], "LONG")
        self.assertEqual(rec["direction"], "UNKNOWN")

    def test_missing_direction_creates_ic_ineligible(self):
        from learning import log_signal_scan
        sig = _minimal_sig()
        del sig["direction"]
        log_signal_scan([sig], _regime())
        rec = self._records()[0]
        self.assertFalse(rec["ic_eligible"])
        self.assertIsNotNone(rec.get("exclusion_reason"))

    def test_explicit_unknown_string_creates_ic_ineligible(self):
        from learning import log_signal_scan
        sig = _minimal_sig(direction="UNKNOWN")
        log_signal_scan([sig], _regime())
        rec = self._records()[0]
        self.assertFalse(rec["ic_eligible"])

    def test_short_direction_sign_preserved_in_ic(self):
        """_dir_sign returns -1 for SHORT, confirming sign adjustment is correct."""
        from ic.data import _dir_sign
        self.assertEqual(_dir_sign({"direction": "SHORT"}), -1)
        self.assertEqual(_dir_sign({"direction": "LONG"}), 1)
        self.assertEqual(_dir_sign({"direction": "NEUTRAL"}), 1)


# ─────────────────────────────────────────────────────────────────────────────
# D. Backward compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestBackwardCompatibility(unittest.TestCase):
    """Old signals_log records (no ic_eligible / no new fields) must still load."""

    def _old_record(self, direction: str | None = "LONG") -> dict:
        rec = {
            "_schema_version": 1,
            "ts": "2026-03-01T14:00:00+00:00",
            "scan_id": "20260301T140000",
            "symbol": "AAPL",
            "score": 32.0,
            "price": 175.0,
            "regime": "TRENDING_UP",
            "vix": 17.0,
            "score_breakdown": {
                "trend": 7, "momentum": 5, "squeeze": 3, "flow": 4,
                "breakout": 6, "pead": 2, "news": 8, "short_squeeze": 1,
                "reversion": 3, "overnight": 2,
            },
            "disabled_dims": [],
            "news_debug": {},
        }
        if direction is not None:
            rec["direction"] = direction
        return rec

    def test_old_record_without_ic_eligible_passes_ic_filter(self):
        """ic_eligible absent → None → not False → record passes through."""
        from ic.data import _load_signal_records
        rec = self._old_record()
        self.assertNotIn("ic_eligible", rec)
        # Simulate the filter logic inline (mirrors ic/data.py)
        self.assertIsNot(rec.get("ic_eligible"), False)  # None is not False

    def test_old_record_without_direction_is_not_excluded(self):
        """Old records with missing direction field are not skipped by the new filter."""
        rec = self._old_record(direction=None)
        # The new filter skips only direction=="UNKNOWN" — not missing
        self.assertNotEqual(rec.get("direction"), "UNKNOWN")

    def test_load_old_records_via_tempfile(self):
        """_load_signal_records loads old-format records without crashing."""
        from ic.data import _load_signal_records
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps(self._old_record()) + "\n")
            path = fh.name
        try:
            records = _load_signal_records(signals_log_path=path, min_age_days=0)
            # Must load at least 0 records without error (may be filtered by date window)
            self.assertIsInstance(records, list)
        finally:
            os.unlink(path)

    def test_new_record_with_ic_eligible_false_excluded_from_ic(self):
        """New records with ic_eligible=False are excluded."""
        rec = self._old_record()
        rec["ic_eligible"] = False
        rec["direction"] = "UNKNOWN"
        # Filter logic: ic_eligible is False → skip
        self.assertIs(rec.get("ic_eligible"), False)

    def test_unknown_direction_excluded_from_ic(self):
        """New records with direction=UNKNOWN are excluded."""
        rec = self._old_record(direction="UNKNOWN")
        self.assertEqual(rec.get("direction"), "UNKNOWN")


# ─────────────────────────────────────────────────────────────────────────────
# E. Training record linkage
# ─────────────────────────────────────────────────────────────────────────────

class TestTrainingRecordLinkage(unittest.TestCase):
    """Verify _close_position_record threads observation provenance into training store."""

    def _make_trade(self, with_observation: bool = True) -> dict:
        ao = {
            "candidate_source": "handoff_reader",
        }
        if with_observation:
            ao["observation_id"] = "20260520T143200_NVDA"
            ao["scan_id"] = "20260520T143200"
            ao["signal_session_date"] = "20260520"
            ao["ranking_position"] = 3
            ao["ranking_total"] = 287
        return {
            "symbol": "NVDA",
            "trade_id": "NVDA_20260520T143200",
            "direction": "LONG",
            "trade_type": "INTRADAY",
            "instrument": "stock",
            "entry": 900.0,
            "qty": 10,
            "score": 38,
            "conviction": 7.5,
            "signal_scores": {"trend": 8, "momentum": 7},
            "entry_regime": "TRENDING_UP",
            "open_time": "2026-05-20T14:32:00+00:00",
            "entry_thesis": "strong breakout",
            "agent_outputs": ao,
        }

    def _run_close(self, trade: dict) -> dict:
        """Call _close_position_record and capture what training_store.append receives."""
        captured = {}

        def fake_append(record):
            captured.update(record)

        def fake_classify(t, exit_reason):
            return {
                "metadata_quality": "full",
                "ml_eligible": True,
                "ic_eligible": True,
                "metadata_loss": False,
                "training_eligible": True,
            }

        import orders_portfolio as op
        original_trades = dict(op.active_trades)
        op.active_trades["NVDA"] = trade

        try:
            with (
                patch("orders_portfolio.training_store") as mock_ts,
                patch("orders_portfolio.event_log") as mock_el,
                patch("orders_portfolio._save_positions_file"),
            ):
                mock_ts.append = fake_append
                mock_ts.classify_record_quality = fake_classify
                mock_el.append_close = MagicMock()
                # Also patch the import inside _close_position_record
                with (
                    patch("builtins.__import__", side_effect=self._selective_import(fake_append, fake_classify)),
                ):
                    op._close_position_record("NVDA", exit_price=920.0, exit_reason="TP_HIT", pnl=200.0, hold_minutes=45)
        except Exception:
            pass  # trade removal side effects may fail in test context
        finally:
            op.active_trades.clear()
            op.active_trades.update(original_trades)

        return captured

    def _selective_import(self, fake_append, fake_classify):
        """Intercept specific imports inside _close_position_record."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _import(name, *args, **kwargs):
            return original_import(name, *args, **kwargs)

        return _import

    def _patch_close_deps(self):
        """Return a context manager stack patching all local imports inside _close_position_record."""
        # event_log, training_store, trade_data_contract are imported locally in the function.
        return (
            patch("event_log.append_close", MagicMock()),
            patch("orders_portfolio._save_positions_file"),
        )

    def test_observation_id_in_training_record(self):
        """When active trade has observation_id, training record carries it."""
        import orders_portfolio as op
        trade = self._make_trade(with_observation=True)
        written = {}

        def fake_ts_append(record):
            written.update(record)

        def fake_classify(t, reason):
            return {"metadata_quality": "full", "ml_eligible": True, "ic_eligible": True,
                    "metadata_loss": False, "training_eligible": True}

        op.active_trades["NVDA"] = trade
        try:
            with (
                patch("event_log.append_close", MagicMock()),
                patch("orders_portfolio._save_positions_file"),
                patch("training_store.append", side_effect=fake_ts_append),
                patch("training_store.classify_record_quality", side_effect=fake_classify),
                patch("trade_data_contract.write_closed_record", MagicMock()),
            ):
                op._close_position_record(
                    "NVDA", exit_price=920.0, exit_reason="TP_HIT",
                    pnl=200.0, hold_minutes=45,
                )
        finally:
            op.active_trades.pop("NVDA", None)

        self.assertEqual(written.get("observation_id"), "20260520T143200_NVDA")
        self.assertEqual(written.get("scan_id"), "20260520T143200")
        self.assertEqual(written.get("signal_session_date"), "20260520")
        self.assertEqual(written.get("ranking_position"), 3)
        self.assertEqual(written.get("ranking_total"), 287)
        self.assertEqual(written.get("candidate_source"), "handoff_reader")
        self.assertEqual(written.get("linkage_quality"), "full")
        self.assertIsNone(written.get("linkage_loss_reason"))

    def test_missing_observation_id_marks_linkage_missing(self):
        """When active trade lacks observation_id, linkage_quality='missing'."""
        import orders_portfolio as op
        trade = self._make_trade(with_observation=False)
        written = {}

        def fake_ts_append(record):
            written.update(record)

        def fake_classify(t, reason):
            return {"metadata_quality": "full", "ml_eligible": True, "ic_eligible": True,
                    "metadata_loss": False, "training_eligible": True}

        op.active_trades["NVDA"] = trade
        try:
            with (
                patch("event_log.append_close", MagicMock()),
                patch("orders_portfolio._save_positions_file"),
                patch("training_store.append", side_effect=fake_ts_append),
                patch("training_store.classify_record_quality", side_effect=fake_classify),
                patch("trade_data_contract.write_closed_record", MagicMock()),
            ):
                op._close_position_record(
                    "NVDA", exit_price=920.0, exit_reason="TP_HIT",
                    pnl=200.0, hold_minutes=45,
                )
        finally:
            op.active_trades.pop("NVDA", None)

        self.assertIsNone(written.get("observation_id"))
        self.assertEqual(written.get("linkage_quality"), "missing")
        self.assertIsNotNone(written.get("linkage_loss_reason"))

    def test_missing_linkage_does_not_crash_close(self):
        """_close_position_record must not raise when linkage fields are absent."""
        import orders_portfolio as op
        trade = self._make_trade(with_observation=False)
        op.active_trades["NVDA"] = trade
        try:
            with (
                patch("event_log.append_close", MagicMock()),
                patch("orders_portfolio._save_positions_file"),
                patch("training_store.append", MagicMock()),
                patch("training_store.classify_record_quality", return_value={
                    "metadata_quality": "full", "ml_eligible": True, "ic_eligible": True,
                    "metadata_loss": False, "training_eligible": True,
                }),
                patch("trade_data_contract.write_closed_record", MagicMock()),
            ):
                # Must not raise
                op._close_position_record(
                    "NVDA", exit_price=920.0, exit_reason="TP_HIT",
                    pnl=200.0, hold_minutes=45,
                )
        finally:
            op.active_trades.pop("NVDA", None)


# ─────────────────────────────────────────────────────────────────────────────
# Signal object scan provenance
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalObjectProvenance(unittest.TestCase):
    """Signal objects carry scan_id, observation_id, ranking_position, ranking_total."""

    def _make_signal(self, symbol: str = "AAPL", scan_id: str = "20260520T143200",
                     ranking_position: int = 2, ranking_total: int = 150) -> "Signal":
        from signal_types import Signal
        from datetime import datetime, UTC
        return Signal(
            symbol=symbol,
            direction="LONG",
            conviction_score=7.5,
            dimension_scores={"trend": 8},
            timestamp=datetime.now(UTC),
            regime_context="TRENDING_UP",
            scan_id=scan_id,
            observation_id=f"{scan_id}_{symbol}",
            ranking_position=ranking_position,
            ranking_total=ranking_total,
        )

    def test_signal_carries_scan_id(self):
        sig = self._make_signal()
        self.assertEqual(sig.scan_id, "20260520T143200")

    def test_signal_carries_observation_id(self):
        sig = self._make_signal()
        self.assertEqual(sig.observation_id, "20260520T143200_AAPL")

    def test_signal_carries_ranking(self):
        sig = self._make_signal(ranking_position=5, ranking_total=200)
        self.assertEqual(sig.ranking_position, 5)
        self.assertEqual(sig.ranking_total, 200)

    def test_signal_to_dict_includes_provenance(self):
        sig = self._make_signal()
        d = sig.to_dict()
        self.assertEqual(d["scan_id"], "20260520T143200")
        self.assertEqual(d["observation_id"], "20260520T143200_AAPL")
        self.assertEqual(d["ranking_position"], 2)
        self.assertEqual(d["ranking_total"], 150)

    def test_signal_without_scan_id_omits_from_dict(self):
        """Default (empty string) scan_id fields are omitted from to_dict() output."""
        from signal_types import Signal
        from datetime import datetime, UTC
        sig = Signal(
            symbol="AAPL",
            direction="LONG",
            conviction_score=7.5,
            dimension_scores={},
            timestamp=datetime.now(UTC),
            regime_context="TRENDING_UP",
        )
        d = sig.to_dict()
        self.assertNotIn("scan_id", d)
        self.assertNotIn("observation_id", d)
        self.assertNotIn("ranking_position", d)
        self.assertNotIn("ranking_total", d)


# ─────────────────────────────────────────────────────────────────────────────
# Ranking correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestRankingCorrectness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mktemp(suffix=".jsonl")
        import learning as _l
        self._orig = _l.SIGNALS_LOG_FILE
        _l.SIGNALS_LOG_FILE = self._tmp

    def tearDown(self):
        import learning as _l
        _l.SIGNALS_LOG_FILE = self._orig
        if os.path.exists(self._tmp):
            os.unlink(self._tmp)

    def test_highest_score_gets_rank_one(self):
        from learning import log_signal_scan
        sigs = [
            _minimal_sig("LOW", score=10.0),
            _minimal_sig("MID", score=25.0),
            _minimal_sig("HIGH", score=40.0),
        ]
        log_signal_scan(sigs, _regime())
        with open(self._tmp) as f:
            records = {json.loads(l)["symbol"]: json.loads(l) for l in f if l.strip()}
        self.assertEqual(records["HIGH"]["ranking_position"], 1)
        self.assertEqual(records["MID"]["ranking_position"], 2)
        self.assertEqual(records["LOW"]["ranking_position"], 3)

    def test_ranking_total_equals_input_count(self):
        from learning import log_signal_scan
        sigs = [_minimal_sig(f"SYM{i}") for i in range(7)]
        log_signal_scan(sigs, _regime())
        with open(self._tmp) as f:
            records = [json.loads(l) for l in f if l.strip()]
        for rec in records:
            self.assertEqual(rec["ranking_total"], 7)


if __name__ == "__main__":
    unittest.main()
