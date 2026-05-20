"""
tests/test_ml_outcome_joiner.py — Sprint 3 proof tests for ml_outcome_joiner.py

T1  — Module loads without ML-library imports (sklearn, torch, joblib, etc.)
T2  — Empty observations file → 0 records, summary zeros, output written
T3  — Missing observations file → empty dataset, no crash
T4  — Observation with no matching ORDER_INTENT → pass row (trade_taken=False)
T5  — Exact join via observation_id → join_quality="exact", order_intent_seen=True
T6  — Fallback join via symbol+direction+timestamp → join_quality="fallback"
T7  — Full chain (obs + intent + fill + close + outcome) → ml_eligible=True
T8  — Missing signal_scores → ml_eligible=False, exclusion_reason set
T9  — Direction=NEUTRAL → ml_eligible=False, exclusion_reason set
T10 — Fallback join → ml_eligible=False (join_quality must be "exact" for eligibility)
T11 — LEAKAGE_FIELDS and ML_FEATURE_FIELDS are disjoint (no leakage in feature set)
T12 — WIN/LOSS/BREAKEVEN label logic: >0→WIN, <0→LOSS, ==0→BREAKEVEN
T13 — BREAKEVEN (pnl_pct==0.0) is NOT labelled WIN
T14 — hold_minutes is in the record but NOT in ML_FEATURE_FIELDS
T15 — Summary counts are consistent with the record list
T16 — Output JSONL and summary JSON are written to specified paths
T17 — Pass rows (trade_taken=False) have outcome_label=None, not WIN or LOSS
T18 — pnl_pct=None → outcome_label=None, ml_eligible=False
T19 — Outcome from training_records.jsonl (ledger absent) → pnl_pct extracted
T20 — Ledger takes precedence over training_records when both have same trade_id
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# ── Path setup (flat project — no package) ────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.ml_outcome_joiner import (   # noqa: E402
    BREAKEVEN_THRESHOLD,
    LEAKAGE_FIELDS,
    ML_FEATURE_FIELDS,
    SCHEMA_VERSION,
    join_outcomes,
    _outcome_label,
    _fallback_join,
    _exact_join,
    _index_events,
    _index_outcomes,
    _extract_outcome,
    _classify_eligible,
)


# ── Fixture helpers ────────────────────────────────────────────────────────────

_BASE_TS = "2026-05-20T10:00:00+00:00"


def _ts_offset(seconds: int = 0) -> str:
    """Return an ISO timestamp offset from _BASE_TS by `seconds`."""
    dt = datetime.fromisoformat(_BASE_TS) + timedelta(seconds=seconds)
    return dt.isoformat()


def _make_observation(
    symbol: str = "AAPL",
    direction: str = "LONG",
    observation_id: str = "20260520T100000_AAPL",
    scan_id: str = "20260520T100000",
    signal_scores: dict | None = None,
    session_date: str = "2026-05-20",
    timestamp_utc: str = _BASE_TS,
    passed_base_threshold: bool = True,
) -> dict:
    scores = signal_scores if signal_scores is not None else {"trend": 8, "momentum": 6}
    obs = {
        "schema_version": "sprint2_v1",
        "timestamp_utc": timestamp_utc,
        "session_date": session_date,
        "scan_id": scan_id,
        "observation_id": observation_id,
        "symbol": symbol,
        "direction": direction,
        "candidate_source": "committed_universe",
        "base_score": 42.0,
        "ranking_position": 1,
        "ranking_total": 20,
        "signal_scores": scores,
        "regime": "TRENDING_UP",
        "vix": 18.5,
        "time_of_day": "10:00",
        "day_of_week": "Wednesday",
        "is_after_hours": False,
        "passed_base_threshold": passed_base_threshold,
        "ml_observer_enabled": True,
        "ml_score_influence_enabled": False,
        "ml_inference_eligible": False,
        "exclusion_reason": "prediction_not_implemented_sprint_2",
        "order_intent_linked": False,
    }
    if scores:
        for k, v in scores.items():
            obs[f"dim_{k}"] = v
    return obs


def _make_intent(
    trade_id: str = "AAPL_20260520_100010_001",
    symbol: str = "AAPL",
    direction: str = "LONG",
    observation_id: str = "20260520T100000_AAPL",
    ts: str = _BASE_TS,
) -> dict:
    return {
        "ts": ts,
        "event": "ORDER_INTENT",
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "trade_type": "SWING",
        "intended_price": 190.0,
        "qty": 50,
        "sl": 185.0,
        "tp": 200.0,
        "regime": "TRENDING_UP",
        "signal_scores": {"trend": 8, "momentum": 6},
        "conviction": 0.65,
        "reasoning": "Strong trend momentum",
        "score": 42,
        "observation_id": observation_id,
    }


def _make_fill(
    trade_id: str = "AAPL_20260520_100010_001",
    symbol: str = "AAPL",
    fill_price: float = 190.10,
    fill_qty: int = 50,
    ts: str | None = None,
) -> dict:
    return {
        "ts": ts or _ts_offset(30),
        "event": "ORDER_FILLED",
        "trade_id": trade_id,
        "symbol": symbol,
        "fill_price": fill_price,
        "fill_qty": fill_qty,
        "order_id": 99001,
    }


def _make_close(
    trade_id: str = "AAPL_20260520_100010_001",
    symbol: str = "AAPL",
    exit_price: float = 193.50,
    pnl: float = 170.0,
    exit_reason: str = "tp_hit",
    hold_minutes: int = 45,
    ts: str | None = None,
) -> dict:
    return {
        "ts": ts or _ts_offset(2700),
        "event": "POSITION_CLOSED",
        "trade_id": trade_id,
        "symbol": symbol,
        "exit_price": exit_price,
        "pnl": pnl,
        "exit_reason": exit_reason,
        "hold_minutes": hold_minutes,
    }


def _make_outcome_record(
    trade_id: str = "AAPL_20260520_100010_001",
    pnl_pct: float = 0.0178,
    exit_price: float = 193.50,
    exit_reason: str = "tp_hit",
    hold_minutes: int = 45,
    schema: str = "training",
) -> dict:
    """Build an outcome record in either training_records or ledger format."""
    if schema == "ledger":
        return {
            "schema_version": "1.0",
            "trade_id": trade_id,
            "symbol": "AAPL",
            "direction": "LONG",
            "fill_price": 190.10,
            "fill_qty": 50,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "realised_pnl": pnl_pct * 190.10 * 50,
            "exit_reason": exit_reason,
            "hold_minutes": hold_minutes,
            "ts_fill": _BASE_TS,
            "ts_exit": _ts_offset(2700),
            "ts_written": _ts_offset(2700),
            "win_loss_label": "WIN" if pnl_pct > 0 else ("LOSS" if pnl_pct < 0 else "BREAKEVEN"),
        }
    else:   # training_records format
        return {
            "trade_id": trade_id,
            "symbol": "AAPL",
            "direction": "LONG",
            "fill_price": 190.10,
            "exit_price": exit_price,
            "pnl": pnl_pct * 190.10 * 50,
            "pnl_pct": pnl_pct,
            "exit_reason": exit_reason,
            "hold_minutes": hold_minutes,
            "ts_fill": _BASE_TS,
            "ts_close": _ts_offset(2700),
            "ts_written": _ts_offset(2700),
        }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ── T1: No ML-library imports ─────────────────────────────────────────────────

def test_t1_no_ml_library_imports():
    """T1: ml_outcome_joiner imports no third-party ML libraries."""
    script_path = Path(PROJECT_ROOT) / "scripts" / "ml_outcome_joiner.py"
    assert script_path.exists(), "scripts/ml_outcome_joiner.py not found"
    import_lines = [
        line.strip()
        for line in script_path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    banned = ["sklearn", "torch", "joblib", "tensorflow", "keras", "xgboost", "lightgbm"]
    for banned_lib in banned:
        for line in import_lines:
            assert banned_lib not in line, (
                f"T1 FAIL: '{banned_lib}' found in import line: {line!r}"
            )


# ── T2: Empty observations → empty output ─────────────────────────────────────

def test_t2_empty_observations_writes_empty_output():
    """T2: Empty ml_observations.jsonl → 0 records, summary zeros, output created."""
    with tempfile.TemporaryDirectory() as tmp:
        obs_path = Path(tmp) / "ml_observations.jsonl"
        obs_path.write_text("")   # empty file
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"

        records, summary = join_outcomes(
            obs_path=obs_path, output_path=out_path, summary_path=summ_path,
        )

        assert records == []
        assert summary["total_observations"] == 0
        assert summary["ml_eligible"] == 0
        assert out_path.exists()
        assert summ_path.exists()
        written = _read_jsonl(out_path)
        assert written == []


# ── T3: Missing observations file → empty dataset ────────────────────────────

def test_t3_missing_observations_file_no_crash():
    """T3: Missing ml_observations.jsonl → empty dataset, no crash."""
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"

        records, summary = join_outcomes(
            obs_path=Path(tmp) / "nonexistent.jsonl",
            output_path=out_path,
            summary_path=summ_path,
        )

        assert records == []
        assert summary["total_observations"] == 0
        assert out_path.exists()


# ── T4: No ORDER_INTENT match → pass row ─────────────────────────────────────

def test_t4_no_intent_match_is_pass_row():
    """T4: Observation with no matching ORDER_INTENT → pass row."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        obs_path = Path(tmp) / "obs.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"
        _write_jsonl(obs_path, [obs])

        records, summary = join_outcomes(
            obs_path=obs_path,
            events_path=Path(tmp) / "no_events.jsonl",
            output_path=out_path,
            summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["trade_taken"] is False
        assert rec["order_intent_seen"] is False
        assert rec["outcome_label"] is None
        assert rec["ml_eligible"] is False
        assert rec["exclusion_reason"] == "no_realised_trade_outcome"
        assert rec["join_quality"] == "no_match"
        assert summary["no_match"] == 1
        assert summary["trade_taken"] == 0


# ── T5: Exact join via observation_id ─────────────────────────────────────────

def test_t5_exact_join_via_observation_id():
    """T5: Observation_id present in ORDER_INTENT → exact join."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        intent = _make_intent()
        obs_path = Path(tmp) / "obs.jsonl"
        events_path = Path(tmp) / "events.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"
        _write_jsonl(obs_path, [obs])
        _write_jsonl(events_path, [intent])

        records, summary = join_outcomes(
            obs_path=obs_path, events_path=events_path,
            output_path=out_path, summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["join_quality"] == "exact"
        assert rec["order_intent_seen"] is True
        assert rec["trade_taken"] is True
        assert rec["trade_id"] == "AAPL_20260520_100010_001"
        assert summary["joined_exact"] == 1
        assert summary["joined_fallback"] == 0


# ── T6: Fallback join via timestamp proximity ─────────────────────────────────

def test_t6_fallback_join_via_timestamp():
    """T6: No observation_id in ORDER_INTENT → fallback by symbol+direction+timestamp."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        # Intent without observation_id (pre-Sprint 2)
        intent = _make_intent()
        intent.pop("observation_id", None)
        intent["ts"] = _ts_offset(60)   # 60s after observation — within 300s window

        obs_path = Path(tmp) / "obs.jsonl"
        events_path = Path(tmp) / "events.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"
        _write_jsonl(obs_path, [obs])
        _write_jsonl(events_path, [intent])

        records, summary = join_outcomes(
            obs_path=obs_path, events_path=events_path,
            output_path=out_path, summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["join_quality"] == "fallback"
        assert rec["order_intent_seen"] is True
        assert summary["joined_fallback"] == 1


# ── T7: Full chain → ml_eligible=True ────────────────────────────────────────

def test_t7_full_chain_ml_eligible_true():
    """T7: obs + intent + fill + close + outcome → ml_eligible=True, WIN label."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        intent = _make_intent()
        fill = _make_fill()
        close_ev = _make_close()
        outcome = _make_outcome_record(pnl_pct=0.0178)

        obs_path = Path(tmp) / "obs.jsonl"
        events_path = Path(tmp) / "events.jsonl"
        ledger_path = Path(tmp) / "ledger.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"

        _write_jsonl(obs_path, [obs])
        _write_jsonl(events_path, [intent, fill, close_ev])
        _write_jsonl(ledger_path, [_make_outcome_record(pnl_pct=0.0178, schema="ledger")])

        records, summary = join_outcomes(
            obs_path=obs_path, events_path=events_path,
            ledger_path=ledger_path, output_path=out_path, summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["ml_eligible"] is True
        assert rec["exclusion_reason"] is None
        assert rec["outcome_label"] == "WIN"
        assert rec["join_quality"] == "exact"
        assert rec["order_filled"] is True
        assert rec["position_closed"] is True
        assert summary["ml_eligible"] == 1
        assert summary["win_count"] == 1


# ── T8: Missing signal_scores → exclusion ────────────────────────────────────

def test_t8_missing_signal_scores_excluded():
    """T8: Observation with signal_scores=None → ml_eligible=False."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation(signal_scores={})
        obs["signal_scores"] = None  # explicitly null
        # Remove dim_* keys too
        for k in list(obs.keys()):
            if k.startswith("dim_"):
                del obs[k]

        obs_path = Path(tmp) / "obs.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"
        _write_jsonl(obs_path, [obs])

        records, _ = join_outcomes(
            obs_path=obs_path, output_path=out_path, summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["ml_eligible"] is False
        assert rec["exclusion_reason"] == "missing_signal_scores"


# ── T9: Non-directional direction → exclusion ────────────────────────────────

def test_t9_neutral_direction_excluded():
    """T9: direction=NEUTRAL → ml_eligible=False, exclusion_reason set."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation(direction="NEUTRAL")
        intent = _make_intent()

        obs_path = Path(tmp) / "obs.jsonl"
        events_path = Path(tmp) / "events.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"
        _write_jsonl(obs_path, [obs])
        _write_jsonl(events_path, [intent])

        records, _ = join_outcomes(
            obs_path=obs_path, events_path=events_path,
            output_path=out_path, summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["ml_eligible"] is False
        assert rec["exclusion_reason"] == "direction_not_directional"


# ── T10: Fallback join → ml_eligible=False ───────────────────────────────────

def test_t10_fallback_join_not_ml_eligible():
    """T10: Fallback join sets ml_eligible=False (only exact joins are eligible)."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        intent = _make_intent()
        intent.pop("observation_id", None)
        intent["ts"] = _ts_offset(30)
        fill = _make_fill()
        close_ev = _make_close()

        obs_path = Path(tmp) / "obs.jsonl"
        events_path = Path(tmp) / "events.jsonl"
        ledger_path = Path(tmp) / "ledger.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"
        _write_jsonl(obs_path, [obs])
        _write_jsonl(events_path, [intent, fill, close_ev])
        _write_jsonl(ledger_path, [_make_outcome_record(pnl_pct=0.02, schema="ledger")])

        records, _ = join_outcomes(
            obs_path=obs_path, events_path=events_path, ledger_path=ledger_path,
            output_path=out_path, summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["join_quality"] == "fallback"
        assert rec["ml_eligible"] is False
        assert rec["exclusion_reason"] == "fallback_join_not_eligible"


# ── T11: LEAKAGE_FIELDS ∩ ML_FEATURE_FIELDS = ∅ ──────────────────────────────

def test_t11_leakage_and_feature_fields_are_disjoint():
    """T11: LEAKAGE_FIELDS and ML_FEATURE_FIELDS must have no overlap."""
    overlap = LEAKAGE_FIELDS & ML_FEATURE_FIELDS
    assert overlap == set(), (
        f"T11 FAIL: {overlap} appear in both LEAKAGE_FIELDS and ML_FEATURE_FIELDS"
    )


# ── T12: WIN / LOSS / BREAKEVEN label logic ───────────────────────────────────

def test_t12_outcome_label_logic():
    """T12: Correct labels for positive, negative, and zero pnl_pct."""
    assert _outcome_label(0.05)  == "WIN"
    assert _outcome_label(0.001) == "WIN"
    assert _outcome_label(-0.03) == "LOSS"
    assert _outcome_label(-0.001) == "LOSS"
    assert _outcome_label(0.0)   == "BREAKEVEN"
    assert _outcome_label(None)  is None


# ── T13: BREAKEVEN is not WIN ─────────────────────────────────────────────────

def test_t13_breakeven_is_not_win():
    """T13: pnl_pct==0.0 must produce BREAKEVEN, never WIN."""
    label = _outcome_label(0.0)
    assert label == "BREAKEVEN", f"T13 FAIL: got {label!r} instead of BREAKEVEN"
    assert label != "WIN"


# ── T14: hold_minutes not in ML_FEATURE_FIELDS ───────────────────────────────

def test_t14_hold_minutes_not_in_feature_fields():
    """T14: hold_minutes is a leakage field and must NOT appear in ML_FEATURE_FIELDS."""
    assert "hold_minutes" not in ML_FEATURE_FIELDS
    assert "hold_minutes" in LEAKAGE_FIELDS or "holding_minutes" in LEAKAGE_FIELDS


# ── T15: Summary counts match record list ─────────────────────────────────────

def test_t15_summary_counts_consistent():
    """T15: Summary counters match the actual record list produced by join_outcomes."""
    with tempfile.TemporaryDirectory() as tmp:
        # Two observations: one full-chain WIN, one pass row
        obs_win = _make_observation(symbol="AAPL", observation_id="20260520T100000_AAPL")
        obs_pass = _make_observation(symbol="GOOG", observation_id="20260520T100000_GOOG")

        intent = _make_intent(symbol="AAPL", observation_id="20260520T100000_AAPL")
        fill = _make_fill(symbol="AAPL")
        close_ev = _make_close(symbol="AAPL")

        obs_path = Path(tmp) / "obs.jsonl"
        events_path = Path(tmp) / "events.jsonl"
        ledger_path = Path(tmp) / "ledger.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"

        _write_jsonl(obs_path, [obs_win, obs_pass])
        _write_jsonl(events_path, [intent, fill, close_ev])
        _write_jsonl(ledger_path, [_make_outcome_record(pnl_pct=0.018, schema="ledger")])

        records, summary = join_outcomes(
            obs_path=obs_path, events_path=events_path, ledger_path=ledger_path,
            output_path=out_path, summary_path=summ_path,
        )

        actual_ml_eligible = sum(1 for r in records if r.get("ml_eligible"))
        actual_win = sum(1 for r in records if r.get("outcome_label") == "WIN")
        actual_pass = sum(1 for r in records if not r.get("trade_taken"))

        assert summary["total_observations"] == 2
        assert summary["ml_eligible"] == actual_ml_eligible
        assert summary["win_count"] == actual_win
        assert summary["no_match"] == actual_pass


# ── T16: Output files written to specified paths ──────────────────────────────

def test_t16_output_files_created_at_specified_paths():
    """T16: join_outcomes writes both output JSONL and summary JSON."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        obs_path = Path(tmp) / "obs.jsonl"
        out_path = Path(tmp) / "subdir" / "canonical.jsonl"
        summ_path = Path(tmp) / "subdir" / "summary.json"
        _write_jsonl(obs_path, [obs])

        join_outcomes(obs_path=obs_path, output_path=out_path, summary_path=summ_path)

        assert out_path.exists(), "canonical_learning_dataset.jsonl not created"
        assert summ_path.exists(), "canonical_learning_dataset_summary.json not created"
        # Verify JSON parseable
        written = _read_jsonl(out_path)
        assert len(written) == 1
        with open(summ_path) as f:
            summ = json.load(f)
        assert summ["total_observations"] == 1


# ── T17: Pass rows have outcome_label=None ────────────────────────────────────

def test_t17_pass_rows_have_null_outcome_label():
    """T17: Observations with no matched trade → outcome_label=None, never WIN/LOSS."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        obs_path = Path(tmp) / "obs.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"
        _write_jsonl(obs_path, [obs])

        records, _ = join_outcomes(
            obs_path=obs_path,
            events_path=Path(tmp) / "empty_events.jsonl",
            output_path=out_path,
            summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["trade_taken"] is False
        assert rec["outcome_label"] is None
        assert rec["outcome_label"] not in ("WIN", "LOSS", "BREAKEVEN")


# ── T18: pnl_pct=None → outcome_label=None, ml_eligible=False ────────────────

def test_t18_null_pnl_pct_excluded():
    """T18: Full chain but pnl_pct=None → outcome_label=None, ml_eligible=False."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        intent = _make_intent()
        fill = _make_fill()

        obs_path = Path(tmp) / "obs.jsonl"
        events_path = Path(tmp) / "events.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"
        _write_jsonl(obs_path, [obs])
        _write_jsonl(events_path, [intent, fill])  # no close, no outcome record

        records, _ = join_outcomes(
            obs_path=obs_path, events_path=events_path,
            output_path=out_path, summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["outcome_label"] is None
        assert rec["ml_eligible"] is False
        assert rec["realised_pnl_pct"] is None


# ── T19: Outcome from training_records.jsonl ─────────────────────────────────

def test_t19_outcome_from_training_records():
    """T19: When ledger absent, outcome extracted from training_records.jsonl."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        intent = _make_intent()
        fill = _make_fill()
        close_ev = _make_close()
        # training_records format, no ledger
        training_rec = _make_outcome_record(pnl_pct=-0.025, schema="training")

        obs_path = Path(tmp) / "obs.jsonl"
        events_path = Path(tmp) / "events.jsonl"
        training_path = Path(tmp) / "training_records.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"

        _write_jsonl(obs_path, [obs])
        _write_jsonl(events_path, [intent, fill, close_ev])
        _write_jsonl(training_path, [training_rec])

        records, summary = join_outcomes(
            obs_path=obs_path, events_path=events_path, training_path=training_path,
            output_path=out_path, summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["realised_pnl_pct"] == pytest.approx(-0.025)
        assert rec["outcome_label"] == "LOSS"
        assert rec["ml_eligible"] is True
        assert summary["loss_count"] == 1


# ── T20: Ledger takes precedence over training_records ───────────────────────

def test_t20_ledger_takes_precedence_over_training_records():
    """T20: When both sources have the same trade_id, ledger values are used."""
    with tempfile.TemporaryDirectory() as tmp:
        obs = _make_observation()
        intent = _make_intent()
        fill = _make_fill()
        close_ev = _make_close()

        # training_records says LOSS
        training_rec = _make_outcome_record(pnl_pct=-0.05, schema="training")
        # ledger says WIN (takes precedence)
        ledger_rec = _make_outcome_record(pnl_pct=0.032, schema="ledger")

        obs_path = Path(tmp) / "obs.jsonl"
        events_path = Path(tmp) / "events.jsonl"
        training_path = Path(tmp) / "training_records.jsonl"
        ledger_path = Path(tmp) / "ledger.jsonl"
        out_path = Path(tmp) / "output.jsonl"
        summ_path = Path(tmp) / "summary.json"

        _write_jsonl(obs_path, [obs])
        _write_jsonl(events_path, [intent, fill, close_ev])
        _write_jsonl(training_path, [training_rec])
        _write_jsonl(ledger_path, [ledger_rec])

        records, summary = join_outcomes(
            obs_path=obs_path, events_path=events_path,
            training_path=training_path, ledger_path=ledger_path,
            output_path=out_path, summary_path=summ_path,
        )

        assert len(records) == 1
        rec = records[0]
        # Ledger value must win
        assert rec["realised_pnl_pct"] == pytest.approx(0.032)
        assert rec["outcome_label"] == "WIN"
        assert summary["win_count"] == 1
        assert summary["loss_count"] == 0
