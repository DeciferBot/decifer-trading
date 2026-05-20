# tests/test_ml_sprint35_observation_health.py
# Sprint 3.5 — Observation health and gate proof tests (T1–T15).
#
# T1:  ml_observer_enabled=True in live config (Sprint 3.5 activation)
# T2:  ml_score_influence_enabled remains False in live config
# T3:  observation writing does not change signal score
# T4:  observation writing does not change ranking
# T5:  observation writing does not change order eligibility
# T6:  observation writing does not change sizing or execution (import check)
# T7:  health check handles missing observation file gracefully
# T8:  health check validates a good observation file
# T9:  health check flags invalid JSON lines
# T10: health check flags duplicate observation_id within the same scan
# T11: health check confirms live_score_unchanged=true
# T12: health check confirms ml_score_influence_enabled=false
# T13: outcome joiner processes observation rows when no trades are linked
# T14: non-traded pass rows remain ml_eligible=false
# T15: no model training, model loading, prediction, sklearn, or joblib imports introduced

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_obs_record(
    symbol: str = "AAPL",
    scan_id: str = "20260520T120000",
    obs_id: str | None = None,
    base_score: float = 28.0,
    direction: str = "LONG",
    live_score_unchanged: bool = True,
    ml_observer_enabled: bool = True,
    ml_score_influence_enabled: bool = False,
    include_signal_scores: bool = True,
    include_ranking: bool = True,
) -> dict:
    if obs_id is None:
        obs_id = f"{scan_id}_{symbol}"
    rec: dict = {
        "schema_version": "sprint2_v1",
        "timestamp_utc": "2026-05-20T12:00:00+00:00",
        "session_date": "2026-05-20",
        "scan_id": scan_id,
        "observation_id": obs_id,
        "symbol": symbol,
        "direction": direction,
        "candidate_source": "handoff_reader",
        "base_score": base_score,
        "live_score_after_observer": base_score,
        "live_score_unchanged": live_score_unchanged,
        "ml_observer_enabled": ml_observer_enabled,
        "ml_score_influence_enabled": ml_score_influence_enabled,
        "ml_inference_eligible": False,
        "exclusion_reason": "prediction_not_implemented_sprint_2",
        "regime": "TRENDING_UP",
        "vix": 15.0,
        "time_of_day": "12:00",
        "day_of_week": "Wednesday",
        "is_after_hours": False,
        "passed_base_threshold": base_score >= 14,
    }
    if include_signal_scores:
        rec["signal_scores"] = {"trend": 6.0, "momentum": 5.0}
    if include_ranking:
        rec["ranking_position"] = 1
        rec["ranking_total"] = 5
    return rec


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


# ── T1: ml_observer_enabled=True in live config ────────────────────────────────

def test_T1_live_config_ml_observer_enabled_true():
    """config.py must have ml_observer_enabled=True after Sprint 3.5 activation."""
    from config import CONFIG
    assert CONFIG.get("ml_observer_enabled") is True, (
        "ml_observer_enabled must be True in config.py — Sprint 3.5 activated evidence collection"
    )


# ── T2: ml_score_influence_enabled remains False ───────────────────────────────

def test_T2_live_config_ml_score_influence_enabled_false():
    """config.py must have ml_score_influence_enabled=False (score influence NOT activated)."""
    from config import CONFIG
    assert CONFIG.get("ml_score_influence_enabled") is False, (
        "ml_score_influence_enabled must remain False — not activated, requires explicit Amit approval"
    )


# ── T3: observation writing does not change signal score ──────────────────────

def test_T3_observation_writing_does_not_change_signal_score():
    """write_observations() must not mutate the score field on any candidate dict."""
    from ml_observation_writer import write_observations
    from config import CONFIG

    candidates = [
        {
            "symbol": "AAPL", "score": 28.5, "direction": "LONG",
            "scan_id": "20260520T123456", "observation_id": "20260520T123456_AAPL",
            "session_date": "2026-05-20", "passed_base_threshold": True,
            "score_breakdown": {"trend": 6.0, "momentum": 5.0},
        },
    ]
    original_scores = {c["symbol"]: c["score"] for c in candidates}

    with tempfile.TemporaryDirectory() as tmpdir:
        write_observations(
            all_scored=candidates,
            rank_map={"AAPL": 1},
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.0,
            config=CONFIG,
            obs_path=Path(tmpdir) / "obs.jsonl",
        )
    for c in candidates:
        assert c["score"] == original_scores[c["symbol"]], (
            f"Score mutated for {c['symbol']}: expected {original_scores[c['symbol']]}, "
            f"got {c['score']}"
        )


# ── T4: observation writing does not change ranking ───────────────────────────

def test_T4_observation_writing_does_not_change_ranking():
    """write_observations() must not mutate rank_map or stamp ranking_position onto source dicts."""
    from ml_observation_writer import write_observations
    from config import CONFIG

    candidates = [
        {
            "symbol": "AAPL", "score": 30, "direction": "LONG",
            "scan_id": "s1", "observation_id": "s1_AAPL",
            "session_date": "2026-05-20", "passed_base_threshold": True,
        },
        {
            "symbol": "MSFT", "score": 25, "direction": "LONG",
            "scan_id": "s1", "observation_id": "s1_MSFT",
            "session_date": "2026-05-20", "passed_base_threshold": True,
        },
    ]
    rank_map = {"AAPL": 1, "MSFT": 2}
    original_rank_map = dict(rank_map)

    with tempfile.TemporaryDirectory() as tmpdir:
        write_observations(
            all_scored=candidates,
            rank_map=rank_map,
            scan_id="s1",
            regime="TRENDING_UP",
            vix=15.0,
            config=CONFIG,
            obs_path=Path(tmpdir) / "obs.jsonl",
        )
    assert rank_map == original_rank_map, "rank_map must not be mutated by write_observations()"
    for c in candidates:
        assert "ranking_position" not in c, (
            f"{c['symbol']}: ranking_position must not be stamped onto source dict by observer"
        )


# ── T5: observation writing does not change order eligibility ─────────────────

def test_T5_observation_writing_does_not_change_order_eligibility():
    """write_observations() must not mutate passed_base_threshold on any candidate."""
    from ml_observation_writer import write_observations
    from config import CONFIG

    candidates = [
        {
            "symbol": "AAPL", "score": 25, "direction": "LONG",
            "scan_id": "s1", "observation_id": "s1_AAPL",
            "session_date": "2026-05-20", "passed_base_threshold": True,
        },
        {
            "symbol": "WEAK", "score": 5, "direction": "LONG",
            "scan_id": "s1", "observation_id": "s1_WEAK",
            "session_date": "2026-05-20", "passed_base_threshold": False,
        },
    ]
    original_flags = {c["symbol"]: c["passed_base_threshold"] for c in candidates}

    with tempfile.TemporaryDirectory() as tmpdir:
        write_observations(
            all_scored=candidates,
            rank_map={"AAPL": 1, "WEAK": 2},
            scan_id="s1",
            regime="TRENDING_UP",
            vix=15.0,
            config=CONFIG,
            obs_path=Path(tmpdir) / "obs.jsonl",
        )
    for c in candidates:
        assert c["passed_base_threshold"] == original_flags[c["symbol"]], (
            f"passed_base_threshold mutated for {c['symbol']}: "
            f"expected {original_flags[c['symbol']]}, got {c['passed_base_threshold']}"
        )


# ── T6: observation writing does not touch execution or sizing ────────────────

def test_T6_observation_writer_has_no_execution_imports():
    """ml_observation_writer.py must not import any execution, sizing, or ML-model modules."""
    src = (_REPO / "ml_observation_writer.py").read_text(encoding="utf-8")
    import_lines = [
        ln.lstrip() for ln in src.splitlines()
        if ln.lstrip().startswith(("import ", "from "))
    ]
    forbidden = [
        "execute_buy", "execute_short", "execute_sell",
        "orders_core", "orders_state", "risk",
        "sklearn", "joblib", "torch", "pickle",
    ]
    for mod in forbidden:
        for ln in import_lines:
            assert mod not in ln, (
                f"ml_observation_writer.py must not import '{mod}' — found in: {ln!r}"
            )


# ── T7: health check handles missing observation file gracefully ───────────────

def test_T7_health_check_missing_file():
    """run_health_check() must handle a missing observations file without crashing."""
    from scripts.ml_observation_health_check import run_health_check

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        summary = run_health_check(
            obs_path=tmp / "nonexistent.jsonl",
            summary_path=tmp / "summary.json",
            report_path=tmp / "report.md",
        )
        assert summary["observation_file_exists"] is False
        assert summary["total_observations"] == 0
        assert summary["invalid_json_lines"] == 0
        assert summary["unique_scan_ids"] == 0
        assert summary["unique_symbols"] == 0
        assert (tmp / "summary.json").exists(), "Summary JSON must be written even when file is absent"
        assert (tmp / "report.md").exists(), "Markdown report must be written even when file is absent"


# ── T8: health check validates a good observation file ────────────────────────

def test_T8_health_check_good_file():
    """run_health_check() correctly counts all fields in a valid observation file."""
    from scripts.ml_observation_health_check import run_health_check

    records = [
        _make_obs_record("AAPL", scan_id="s1", obs_id="s1_AAPL"),
        _make_obs_record("MSFT", scan_id="s1", obs_id="s1_MSFT"),
        _make_obs_record("GOOG", scan_id="s2", obs_id="s2_GOOG"),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        obs_path = tmp / "obs.jsonl"
        _write_jsonl(obs_path, records)

        summary = run_health_check(
            obs_path=obs_path,
            summary_path=tmp / "summary.json",
            report_path=tmp / "report.md",
        )
        assert summary["observation_file_exists"] is True
        assert summary["total_observations"] == 3
        assert summary["unique_scan_ids"] == 2
        assert summary["unique_symbols"] == 3
        assert summary["records_with_observation_id"] == 3
        assert summary["records_with_scan_id"] == 3
        assert summary["records_with_signal_scores"] == 3
        assert summary["records_missing_signal_scores"] == 0
        assert summary["records_with_ranking_position"] == 3
        assert summary["records_with_ranking_total"] == 3
        assert summary["records_with_candidate_source"] == 3
        assert summary["records_with_base_score"] == 3
        assert summary["records_where_live_score_unchanged_true"] == 3
        assert summary["records_with_ml_observer_enabled_true"] == 3
        assert summary["records_with_ml_score_influence_enabled_false"] == 3
        assert summary["invalid_json_lines"] == 0
        assert summary["duplicate_observation_ids"] == []


# ── T9: health check flags invalid JSON lines ─────────────────────────────────

def test_T9_health_check_flags_invalid_json():
    """run_health_check() reports invalid JSON lines and still processes valid lines."""
    from scripts.ml_observation_health_check import run_health_check

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        obs_path = tmp / "obs.jsonl"
        obs_path.write_text(
            json.dumps(_make_obs_record("AAPL")) + "\n"
            "THIS IS NOT JSON\n"
            + json.dumps(_make_obs_record("MSFT")) + "\n",
            encoding="utf-8",
        )
        summary = run_health_check(
            obs_path=obs_path,
            summary_path=tmp / "summary.json",
            report_path=tmp / "report.md",
        )
        assert summary["invalid_json_lines"] == 1, (
            f"Expected 1 invalid JSON line, got {summary['invalid_json_lines']}"
        )
        assert summary["total_observations"] == 2, (
            "Valid lines must still be counted despite one invalid line"
        )


# ── T10: health check flags duplicate observation_id within the same scan ──────

def test_T10_health_check_flags_duplicate_obs_ids():
    """run_health_check() catches duplicate observation_ids within the same scan_id."""
    from scripts.ml_observation_health_check import run_health_check

    records = [
        _make_obs_record("AAPL", scan_id="s1", obs_id="s1_AAPL"),
        _make_obs_record("AAPL", scan_id="s1", obs_id="s1_AAPL"),  # duplicate
        _make_obs_record("MSFT", scan_id="s1", obs_id="s1_MSFT"),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        obs_path = tmp / "obs.jsonl"
        _write_jsonl(obs_path, records)
        summary = run_health_check(
            obs_path=obs_path,
            summary_path=tmp / "summary.json",
            report_path=tmp / "report.md",
        )
        assert len(summary["duplicate_observation_ids"]) >= 1, (
            "Duplicate observation_id within same scan_id must be reported"
        )
        assert "s1_AAPL" in summary["duplicate_observation_ids"]


# ── T11: health check confirms live_score_unchanged=true ─────────────────────

def test_T11_health_check_confirms_live_score_unchanged_true():
    """records_where_live_score_unchanged_true counts records where the flag is exactly True."""
    from scripts.ml_observation_health_check import run_health_check

    records = [
        _make_obs_record("AAPL", live_score_unchanged=True),
        _make_obs_record("MSFT", live_score_unchanged=True),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        obs_path = tmp / "obs.jsonl"
        _write_jsonl(obs_path, records)
        summary = run_health_check(
            obs_path=obs_path,
            summary_path=tmp / "summary.json",
            report_path=tmp / "report.md",
        )
        assert summary["records_where_live_score_unchanged_true"] == 2, (
            "Both records have live_score_unchanged=True — count must be 2"
        )


# ── T12: health check confirms ml_score_influence_enabled=false ───────────────

def test_T12_health_check_confirms_ml_score_influence_disabled():
    """records_with_ml_score_influence_enabled_false counts records where the flag is False."""
    from scripts.ml_observation_health_check import run_health_check

    records = [
        _make_obs_record("AAPL", ml_score_influence_enabled=False),
        _make_obs_record("MSFT", ml_score_influence_enabled=False),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        obs_path = tmp / "obs.jsonl"
        _write_jsonl(obs_path, records)
        summary = run_health_check(
            obs_path=obs_path,
            summary_path=tmp / "summary.json",
            report_path=tmp / "report.md",
        )
        assert summary["records_with_ml_score_influence_enabled_false"] == 2, (
            "Both records have ml_score_influence_enabled=False — count must be 2"
        )


# ── T13: outcome joiner processes observation rows with no trade links ─────────

def test_T13_outcome_joiner_handles_unlinked_observations():
    """join_outcomes() produces pass rows for observations with no matching ORDER_INTENT."""
    from scripts.ml_outcome_joiner import join_outcomes

    observations = [
        _make_obs_record("AAPL", scan_id="s1", obs_id="s1_AAPL"),
        _make_obs_record("MSFT", scan_id="s1", obs_id="s1_MSFT"),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        obs_path = tmp / "obs.jsonl"
        _write_jsonl(obs_path, observations)

        records, summary = join_outcomes(
            obs_path=obs_path,
            events_path=tmp / "no_events.jsonl",
            training_path=tmp / "no_training.jsonl",
            ledger_path=tmp / "no_ledger.jsonl",
            output_path=tmp / "dataset.jsonl",
            summary_path=tmp / "summary.json",
        )
        assert len(records) == 2, "All observations must produce canonical records"
        assert summary["total_observations"] == 2
        assert summary["no_match"] == 2, "Both must be unmatched (no events file)"
        assert all(not r["trade_taken"] for r in records), "All must be pass rows"


# ── T14: non-traded pass rows remain ml_eligible=false ────────────────────────

def test_T14_non_traded_pass_rows_are_ml_ineligible():
    """Pass rows (trade_taken=False) must have ml_eligible=False and outcome_label=None."""
    from scripts.ml_outcome_joiner import join_outcomes

    observations = [_make_obs_record("AAPL", scan_id="s1", obs_id="s1_AAPL")]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        obs_path = tmp / "obs.jsonl"
        _write_jsonl(obs_path, observations)

        records, _ = join_outcomes(
            obs_path=obs_path,
            events_path=tmp / "no_events.jsonl",
            training_path=tmp / "no_training.jsonl",
            ledger_path=tmp / "no_ledger.jsonl",
            output_path=tmp / "dataset.jsonl",
            summary_path=tmp / "summary.json",
        )
        assert len(records) == 1
        rec = records[0]
        assert rec["trade_taken"] is False
        assert rec["ml_eligible"] is False, (
            "Pass rows (no trade) must never be ml_eligible"
        )
        assert rec["outcome_label"] is None, (
            "Pass rows must have outcome_label=None, not WIN or LOSS"
        )


# ── T15: no forbidden imports in any ML sprint module ─────────────────────────

def test_T15_no_forbidden_imports_in_ml_modules():
    """
    None of the ML sprint files may import sklearn, joblib, torch, or model-loading code.
    Checks: ml_observation_writer.py, scripts/ml_outcome_joiner.py,
            scripts/ml_observation_health_check.py
    """
    files_to_check = [
        _REPO / "ml_observation_writer.py",
        _REPO / "scripts" / "ml_outcome_joiner.py",
        _REPO / "scripts" / "ml_observation_health_check.py",
    ]
    forbidden_patterns = ["sklearn", "joblib", "torch", "pickle.load", "model.predict"]

    for filepath in files_to_check:
        if not filepath.exists():
            continue
        src = filepath.read_text(encoding="utf-8")
        import_lines = [
            ln.lstrip() for ln in src.splitlines()
            if ln.lstrip().startswith(("import ", "from "))
        ]
        for pat in forbidden_patterns:
            for ln in import_lines:
                assert pat not in ln, (
                    f"{filepath.name} must not import '{pat}' — found in: {ln!r}"
                )
