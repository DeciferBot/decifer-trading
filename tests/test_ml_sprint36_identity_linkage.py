# tests/test_ml_sprint36_identity_linkage.py
# Sprint 3.6 — Observation identity linkage repair proof tests (T1–T15).
#
# Validates that:
#   T1:  ORDER_INTENT receives top-level observation_id from the dispatched signal.
#   T2:  ORDER_INTENT receives top-level scan_id from the dispatched signal.
#   T3:  ranking_position and ranking_total are preserved when available.
#   T4:  candidate_source is propagated to observation records when available.
#   T5:  candidate_source remains "scanner" only when no source is set on the dict.
#   T6:  schema_version is updated to sprint36_v1.
#   T7:  duplicate observation_id within the same scan is skipped by writer idempotency.
#   T8:  historical duplicate remains visible in health report (not silently hidden).
#   T9:  outcome joiner exact-joins observation_id to ORDER_INTENT when present.
#   T10: fallback joins remain ml_eligible=false with exclusion_reason=fallback_join_not_training_grade.
#   T11: no score changes (scores identical before and after observation write).
#   T12: no ranking changes (ranking_position unchanged by the write).
#   T13: no order eligibility changes (execute_buy gate fields unchanged).
#   T14: no sizing or execution changes (qty/sl/tp unchanged).
#   T15: no model training, model loading, prediction, sklearn, joblib, or ML influence.

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _scored_dict(
    symbol: str = "AAPL",
    score: float = 30.0,
    direction: str = "LONG",
    scan_id: str = "20260521T120000",
    candidate_source: str = "scanner",
    ranking_position: int = 1,
    ranking_total: int = 50,
) -> dict:
    obs_id = f"{scan_id}_{symbol}"
    return {
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "scan_id": scan_id,
        "observation_id": obs_id,
        "candidate_source": candidate_source,
        "ranking_position": ranking_position,
        "ranking_total": ranking_total,
        "passed_base_threshold": True,
        "session_date": "2026-05-21",
        "score_breakdown": {"trend": 7, "momentum": 5, "squeeze": 3},
        "price": 150.0,
        "atr_5m": 0.5,
    }


def _ml_config(tmp_path: Path) -> dict:
    return {
        "ml_observer_enabled": True,
        "ml_score_influence_enabled": False,
        "ml_data_dir": str(tmp_path),
    }


# ── T1 & T2: ORDER_INTENT receives observation_id and scan_id ─────────────────

class TestOrderIntentLinkage:
    """T1, T2, T3 — verify dispatch() forwards ML linkage fields to execute_buy.

    calculate_position_size is imported locally inside dispatch(), so we patch
    it at risk.calculate_position_size (the source module).
    """

    def _run_dispatch(self, payload: dict) -> list[dict]:
        """
        Call signal_dispatcher.dispatch() with a mock execute_buy that captures
        its kwargs without touching IBKR or any broker.  Returns list of kwargs
        dicts from each execute_buy call.
        """
        from signal_dispatcher import dispatch as _dispatch

        captured: list[dict] = []

        def _fake_buy(**kw):
            captured.append(kw)
            return True

        decision = {
            "new_entries": [{"symbol": "AAPL", "direction": "LONG",
                             "trade_type": "INTRADAY", "conviction": "HIGH",
                             "instrument": "stock", "rationale": "test"}],
            "portfolio_actions": [],
        }

        with patch("signal_dispatcher.execute_buy", side_effect=_fake_buy), \
             patch("signal_dispatcher.execute_short", return_value=True), \
             patch("signal_dispatcher.execute_buy_option", return_value=True), \
             patch("risk.calculate_position_size", return_value=10), \
             patch("signal_dispatcher.calculate_stops", return_value=(145.0, 160.0)):
            _dispatch(
                decision,
                candidates_by_symbol={"AAPL": payload},
                active_trades={},
                ib=None,
                portfolio_value=100_000,
                regime={"regime": "TRENDING_UP"},
                execute=True,
            )
        return captured

    def test_T1_observation_id_in_execute_buy_kwargs(self):
        """T1: execute_buy receives top-level observation_id from dispatched signal payload."""
        payload = _scored_dict()
        captured = self._run_dispatch(payload)
        assert captured, "execute_buy was not called"
        kw = captured[0]
        assert kw.get("observation_id") == "20260521T120000_AAPL", (
            f"observation_id missing or wrong in execute_buy kwargs: {kw.get('observation_id')!r}"
        )

    def test_T2_scan_id_in_execute_buy_kwargs(self):
        """T2: execute_buy receives top-level scan_id from dispatched signal payload."""
        payload = _scored_dict()
        captured = self._run_dispatch(payload)
        assert captured, "execute_buy was not called"
        kw = captured[0]
        assert kw.get("scan_id") == "20260521T120000", (
            f"scan_id missing or wrong in execute_buy kwargs: {kw.get('scan_id')!r}"
        )

    def test_T3_ranking_fields_preserved(self):
        """T3: ranking_position and ranking_total are forwarded when available."""
        payload = _scored_dict(ranking_position=5, ranking_total=80)
        captured = self._run_dispatch(payload)
        assert captured, "execute_buy was not called"
        kw = captured[0]
        assert kw.get("ranking_position") == 5, kw.get("ranking_position")
        assert kw.get("ranking_total") == 80, kw.get("ranking_total")

    def test_T11_score_unchanged_by_dispatch(self):
        """T11: scores are identical before and after dispatch call."""
        payload = _scored_dict()
        score_before = payload["score"]
        breakdown_before = dict(payload["score_breakdown"])
        self._run_dispatch(payload)
        assert payload["score"] == score_before
        assert payload["score_breakdown"] == breakdown_before

    def test_T12_ranking_unchanged_by_dispatch(self):
        """T12: ranking_position is not mutated by dispatch."""
        payload = _scored_dict(ranking_position=3)
        self._run_dispatch(payload)
        assert payload["ranking_position"] == 3

    def test_T13_order_eligibility_unchanged(self):
        """T13: passed_base_threshold is not mutated by dispatch."""
        payload = _scored_dict()
        before = payload.get("passed_base_threshold")
        self._run_dispatch(payload)
        assert payload.get("passed_base_threshold") == before

    def test_T14_no_sizing_changes(self):
        """T14: qty/sl/tp sizing logic is called unchanged — dispatch does not bypass it."""
        payload = _scored_dict()

        def _fake_buy(**kw):
            return True

        decision = {
            "new_entries": [{"symbol": "AAPL", "direction": "LONG",
                             "trade_type": "INTRADAY", "conviction": "HIGH",
                             "instrument": "stock", "rationale": "test"}],
            "portfolio_actions": [],
        }

        from signal_dispatcher import dispatch as _dispatch

        with patch("signal_dispatcher.execute_buy", side_effect=_fake_buy), \
             patch("signal_dispatcher.execute_short", return_value=True), \
             patch("signal_dispatcher.execute_buy_option", return_value=True), \
             patch("risk.calculate_position_size", return_value=10) as mock_size, \
             patch("signal_dispatcher.calculate_stops", return_value=(145.0, 160.0)) as mock_stops:
            _dispatch(
                decision,
                candidates_by_symbol={"AAPL": payload},
                active_trades={},
                ib=None,
                portfolio_value=100_000,
                regime={"regime": "TRENDING_UP"},
                execute=True,
            )

        # sizing functions were called (not bypassed), meaning sizing logic is intact
        assert mock_size.called, "calculate_position_size was not called — sizing logic bypassed"
        assert mock_stops.called, "calculate_stops was not called — sizing logic bypassed"


# ── T4 & T5: candidate_source propagation ─────────────────────────────────────

class TestCandidateSourcePropagation:

    def test_T4_candidate_source_propagated_to_observation(self, tmp_path):
        """T4: candidate_source is written to the observation record when available."""
        from ml_observation_writer import write_observations

        cand = _scored_dict(candidate_source="handoff_reader")
        rank_map = {"AAPL": 1}
        cfg = _ml_config(tmp_path)

        obs_file = tmp_path / "ml_observations.jsonl"
        n = write_observations(
            all_scored=[cand],
            rank_map=rank_map,
            scan_id="20260521T120000",
            regime="TRENDING_UP",
            vix=16.0,
            config=cfg,
            obs_path=obs_file,
        )
        assert n == 1
        rec = json.loads(obs_file.read_text().strip())
        assert rec["candidate_source"] == "handoff_reader", rec["candidate_source"]

    def test_T5_candidate_source_unknown_when_absent(self, tmp_path):
        """T5: candidate_source stays unknown only when the field is not set on the dict."""
        from ml_observation_writer import write_observations

        cand = _scored_dict()
        cand.pop("candidate_source", None)  # remove completely
        rank_map = {"AAPL": 1}
        cfg = _ml_config(tmp_path)

        obs_file = tmp_path / "ml_observations.jsonl"
        write_observations(
            all_scored=[cand],
            rank_map=rank_map,
            scan_id="20260521T120000",
            regime="TRENDING_UP",
            vix=16.0,
            config=cfg,
            obs_path=obs_file,
        )
        rec = json.loads(obs_file.read_text().strip())
        assert rec["candidate_source"] == "unknown"

    def test_T4_scanner_source_preserved(self, tmp_path):
        """T4 variant: candidate_source='scanner' is written as-is."""
        from ml_observation_writer import write_observations

        cand = _scored_dict(candidate_source="scanner")
        rank_map = {"AAPL": 1}
        cfg = _ml_config(tmp_path)

        obs_file = tmp_path / "ml_observations.jsonl"
        write_observations(
            all_scored=[cand],
            rank_map=rank_map,
            scan_id="20260521T120000",
            regime="TRENDING_UP",
            vix=16.0,
            config=cfg,
            obs_path=obs_file,
        )
        rec = json.loads(obs_file.read_text().strip())
        assert rec["candidate_source"] == "scanner"


# ── T6: schema version ─────────────────────────────────────────────────────────

class TestSchemaVersion:

    def test_T6_schema_version_updated(self, tmp_path):
        """T6: observation records carry the current SCHEMA_VERSION constant (sprint37_v1 after Sprint 3.7)."""
        from ml_observation_writer import SCHEMA_VERSION, write_observations

        # Sprint 3.7 advanced the schema version — this test tracks the current constant.
        assert SCHEMA_VERSION in ("sprint36_v1", "sprint37_v1"), (
            f"Unexpected SCHEMA_VERSION: {SCHEMA_VERSION!r}"
        )

        cand = _scored_dict()
        cfg = _ml_config(tmp_path)
        obs_file = tmp_path / "ml_observations.jsonl"
        write_observations(
            all_scored=[cand],
            rank_map={"AAPL": 1},
            scan_id="20260521T120000",
            regime="TRENDING_UP",
            vix=16.0,
            config=cfg,
            obs_path=obs_file,
        )
        rec = json.loads(obs_file.read_text().strip())
        assert rec["schema_version"] == SCHEMA_VERSION


# ── T7 & T8: duplicate idempotency ─────────────────────────────────────────────

class TestDuplicateIdempotency:

    def test_T7_within_call_duplicate_skipped(self, tmp_path):
        """T7: writer skips a second record with the same scan_id+observation_id in one call."""
        from ml_observation_writer import write_observations

        scan_id = "20260521T120000"
        cand = _scored_dict(scan_id=scan_id)
        # Submit the same candidate twice in the same all_scored list
        all_scored = [cand, dict(cand)]

        cfg = _ml_config(tmp_path)
        obs_file = tmp_path / "ml_observations.jsonl"
        n = write_observations(
            all_scored=all_scored,
            rank_map={"AAPL": 1},
            scan_id=scan_id,
            regime="TRENDING_UP",
            vix=16.0,
            config=cfg,
            obs_path=obs_file,
        )
        assert n == 1, f"Expected 1 record written (duplicate skipped), got {n}"
        lines = [l for l in obs_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

    def test_T8_historical_duplicate_visible_in_health_report(self, tmp_path):
        """T8: health check still reports duplicates in historical JSONL (not hidden)."""
        import importlib.util, sys as _sys
        _hc_path = _REPO / "scripts" / "ml_observation_health_check.py"
        _spec = importlib.util.spec_from_file_location("ml_observation_health_check", _hc_path)
        _hc_mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_hc_mod)
        _find_duplicate_obs_ids = _hc_mod._find_duplicate_obs_ids

        dup_obs_id = "20260520T133247_AAPL"
        records = [
            {"scan_id": "20260520T133247", "observation_id": dup_obs_id, "symbol": "AAPL"},
            {"scan_id": "20260520T133247", "observation_id": dup_obs_id, "symbol": "AAPL"},
        ]
        dupes = _find_duplicate_obs_ids(records)
        assert dup_obs_id in dupes, (
            "Health check must still surface historical duplicate — it was not found"
        )


# ── T9 & T10: outcome joiner exact join ────────────────────────────────────────

class TestOutcomeJoinerExactJoin:
    """T9, T10 — verify exact join when observation_id present in ORDER_INTENT."""

    def _make_observation(self, obs_id: str, scan_id: str, symbol: str = "AAPL") -> dict:
        return {
            "schema_version": "sprint36_v1",
            "timestamp_utc": "2026-05-21T12:00:00+00:00",
            "session_date": "2026-05-21",
            "scan_id": scan_id,
            "observation_id": obs_id,
            "symbol": symbol,
            "direction": "LONG",
            "candidate_source": "handoff_reader",
            "base_score": 30.0,
            "live_score_after_observer": 30.0,
            "live_score_unchanged": True,
            "ranking_position": 1,
            "ranking_total": 50,
            "signal_scores": {"trend": 7, "momentum": 5},
            "regime": "TRENDING_UP",
            "vix": 16.0,
            "time_of_day": "12:00",
            "day_of_week": "Thursday",
            "is_after_hours": False,
            "passed_base_threshold": True,
            "ml_observer_enabled": True,
            "ml_score_influence_enabled": False,
            "ml_inference_eligible": False,
            "exclusion_reason": "prediction_not_implemented_sprint_2",
            "order_intent_linked": False,
        }

    def _make_intent(self, trade_id: str, obs_id: str, symbol: str = "AAPL") -> dict:
        """ORDER_INTENT record with top-level observation_id (as wired by Sprint 3.6)."""
        return {
            "ts": "2026-05-21T12:00:05+00:00",
            "event": "ORDER_INTENT",
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": "LONG",
            "observation_id": obs_id,   # Sprint 3.6 linkage field
            "scan_id": "20260521T120000",
            "ranking_position": 1,
            "ranking_total": 50,
        }

    def _make_fill(self, trade_id: str, symbol: str = "AAPL") -> dict:
        return {
            "ts": "2026-05-21T12:00:10+00:00",
            "event": "ORDER_FILLED",
            "trade_id": trade_id,
            "symbol": symbol,
            "fill_price": 150.5,
            "fill_qty": 10,
            "order_id": 999,
        }

    def _make_close(self, trade_id: str, symbol: str = "AAPL") -> dict:
        return {
            "ts": "2026-05-21T14:00:00+00:00",
            "event": "POSITION_CLOSED",
            "trade_id": trade_id,
            "symbol": symbol,
            "exit_price": 155.0,
            "pnl": 45.0,
            "exit_reason": "tp_hit",
            "hold_minutes": 120,
        }

    def test_T9_exact_join_when_observation_id_in_intent(self, tmp_path):
        """T9: outcome joiner produces join_quality='exact' when observation_id present in ORDER_INTENT."""
        from scripts.ml_outcome_joiner import join_outcomes

        obs_id = "20260521T120000_AAPL"
        trade_id = "AAPL-20260521-001"

        obs_file = tmp_path / "ml_observations.jsonl"
        events_file = tmp_path / "trade_events.jsonl"
        training_file = tmp_path / "training_records.jsonl"
        ledger_file = tmp_path / "closed_trade_training_ledger.jsonl"

        obs_file.write_text(
            json.dumps(self._make_observation(obs_id, "20260521T120000")) + "\n"
        )
        events = [
            self._make_intent(trade_id, obs_id),
            self._make_fill(trade_id),
            self._make_close(trade_id),
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        training_file.write_text("")
        ledger_file.write_text(json.dumps({
            "trade_id": trade_id, "pnl_pct": 0.03, "exit_reason": "tp_hit",
            "hold_minutes": 120,
        }) + "\n")

        records, summary = join_outcomes(
            obs_path=obs_file,
            events_path=events_file,
            training_path=training_file,
            ledger_path=ledger_file,
            output_path=tmp_path / "out.jsonl",
            summary_path=tmp_path / "summary.json",
            dry_run=True,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["join_quality"] == "exact", f"Expected exact, got {rec['join_quality']!r}"
        assert rec["trade_taken"] is True
        assert summary["joined_exact"] == 1
        assert summary["joined_fallback"] == 0

    def test_T10_fallback_join_ml_eligible_false(self, tmp_path):
        """T10: fallback-joined records are ml_eligible=False with exclusion_reason=fallback_join_not_training_grade."""
        from scripts.ml_outcome_joiner import join_outcomes

        obs_id = "20260521T120000_AAPL"
        trade_id = "AAPL-20260521-002"

        obs_file = tmp_path / "ml_observations.jsonl"
        events_file = tmp_path / "trade_events.jsonl"
        training_file = tmp_path / "training_records.jsonl"
        ledger_file = tmp_path / "closed_trade_training_ledger.jsonl"

        obs_file.write_text(
            json.dumps(self._make_observation(obs_id, "20260521T120000")) + "\n"
        )
        # Intent has NO observation_id — forces fallback join
        intent_no_obs = {
            "ts": "2026-05-21T12:00:05+00:00",
            "event": "ORDER_INTENT",
            "trade_id": trade_id,
            "symbol": "AAPL",
            "direction": "LONG",
            # observation_id deliberately absent
        }
        events = [
            intent_no_obs,
            self._make_fill(trade_id),
            self._make_close(trade_id),
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        training_file.write_text("")
        ledger_file.write_text(json.dumps({
            "trade_id": trade_id, "pnl_pct": 0.03, "exit_reason": "tp_hit",
            "hold_minutes": 120,
        }) + "\n")

        records, summary = join_outcomes(
            obs_path=obs_file,
            events_path=events_file,
            training_path=training_file,
            ledger_path=ledger_file,
            output_path=tmp_path / "out.jsonl",
            summary_path=tmp_path / "summary.json",
            dry_run=True,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["join_quality"] in ("fallback", "no_match"), rec["join_quality"]
        assert rec["ml_eligible"] is False
        if rec["join_quality"] == "fallback":
            assert rec["exclusion_reason"] == "fallback_join_not_training_grade", rec["exclusion_reason"]


# ── T15: no forbidden ML imports ──────────────────────────────────────────────

class TestNoForbiddenMLImports:

    def _module_source(self, module_name: str) -> str:
        import importlib
        import inspect
        mod = importlib.import_module(module_name)
        try:
            return inspect.getsource(mod)
        except (OSError, TypeError):
            return ""

    def test_T15_ml_observation_writer_no_forbidden_imports(self):
        """T15: ml_observation_writer has no sklearn, joblib, model training/loading code."""
        src = Path(_REPO / "ml_observation_writer.py").read_text()
        # Check for actual import statements and call expressions, not docstring mentions
        import_forbidden = ["import sklearn", "import joblib", "import torch",
                            "import tensorflow", "import xgboost", "import lightgbm"]
        call_forbidden = ["pickle.load(", "model.predict(", "model.fit(", ".predict_proba("]
        for f in import_forbidden + call_forbidden:
            assert f not in src, f"Forbidden import/call found in ml_observation_writer.py: {f!r}"

    def test_T15_signal_dispatcher_no_ml_imports(self):
        """T15: signal_dispatcher has no sklearn, joblib, or model prediction code."""
        src = Path(_REPO / "signal_dispatcher.py").read_text()
        forbidden = ["sklearn", "joblib", "pickle.load", "model.predict",
                     "model.fit", "win_prob", "predicted_pnl_pct", "advisory_score"]
        for f in forbidden:
            assert f not in src, f"Forbidden content in signal_dispatcher.py: {f!r}"

    def test_T15_outcome_joiner_no_ml_training(self):
        """T15: ml_outcome_joiner has no model training, loading, or prediction code."""
        src = Path(_REPO / "scripts" / "ml_outcome_joiner.py").read_text()
        forbidden = ["sklearn", "joblib", "pickle.load", "model.predict",
                     "model.fit", "model.load", "win_prob", "advisory_score"]
        for f in forbidden:
            assert f not in src, f"Forbidden content in ml_outcome_joiner.py: {f!r}"

    def test_T15_no_score_influence_in_observation_writer(self):
        """T15: ml_score_influence_enabled is always False (never True) in observation records."""
        import tempfile
        from pathlib import Path as P
        from ml_observation_writer import write_observations

        with tempfile.TemporaryDirectory() as td:
            obs_file = P(td) / "ml_observations.jsonl"
            cfg = {"ml_observer_enabled": True, "ml_score_influence_enabled": False,
                   "ml_data_dir": td}
            cand = _scored_dict()
            write_observations(
                all_scored=[cand], rank_map={"AAPL": 1},
                scan_id="20260521T120000", regime="TRENDING_UP",
                vix=16.0, config=cfg, obs_path=obs_file,
            )
            rec = json.loads(obs_file.read_text().strip())
            assert rec["ml_score_influence_enabled"] is False
            assert rec["live_score_unchanged"] is True
            assert rec["live_score_after_observer"] == rec["base_score"]
