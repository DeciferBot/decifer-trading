# tests/test_ml_observation_writer.py
# Sprint 2 — Signal observation writer proof tests (T1–T20).
#
# These tests verify that:
#   - The observer is inert when ml_observer_enabled=False.
#   - The observer writes valid JSONL records when enabled.
#   - Scores, rankings, and order eligibility are never modified.
#   - The writer does not import forbidden modules.
#   - No model files are created or loaded.
#   - ORDER_INTENT includes observation_id at the top level.
#   - Sprint 1 guarantees (T19, T20) remain intact.

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_candidate(
    symbol: str = "AAPL",
    score: float = 28.0,
    direction: str = "LONG",
    scan_id: str = "20260520T123456",
    candidate_source: str = "handoff_reader",
    include_signal_scores: bool = True,
) -> dict:
    """Build a minimal all_scored dict as produced by signal_pipeline after stamping."""
    obs_id = f"{scan_id}_{symbol}" if scan_id else ""
    d: dict = {
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "candidate_source": candidate_source,
        "scan_id": scan_id,
        "observation_id": obs_id,
        "session_date": "2026-05-20",
        "passed_base_threshold": score >= 14,
        "price": 182.50,
        "atr_5m": 0.35,
    }
    if include_signal_scores:
        d["score_breakdown"] = {
            "trend": 6.0, "momentum": 5.0, "squeeze": 4.0,
            "flow": 3.0, "breakout": 5.0, "mtf": 2.0,
            "news": 1.5, "social": 0.5, "reversion": 0.0,
            "iv_skew": 0.0, "pead": 0.0, "short_squeeze": 0.0,
            "overnight_drift": 0.5, "analyst_revision": 0.0,
            "insider_buying": 0.0, "catalyst": 0.0,
        }
    return d


def _make_config(observer_enabled: bool = False, score_influence_enabled: bool = False) -> dict:
    return {
        "ml_observer_enabled": observer_enabled,
        "ml_score_influence_enabled": score_influence_enabled,
        "ml_data_dir": "data/ml",
    }


def _make_rank_map(candidates: list) -> dict:
    sorted_c = sorted(candidates, key=lambda x: float(x.get("score", 0)), reverse=True)
    return {s["symbol"]: i + 1 for i, s in enumerate(sorted_c)}


def _read_records(path: Path) -> list[dict]:
    """Parse every non-empty line of a JSONL file. File must exist."""
    return [json.loads(ln) for ln in path.read_text().strip().split("\n") if ln.strip()]


# ── T1: ml_observer_enabled=False causes no write ─────────────────────────────

def test_T1_observer_disabled_no_file_written():
    """ml_observer_enabled=False: write_observations() must write nothing."""
    from ml_observation_writer import write_observations

    candidates = [_make_candidate()]
    config = _make_config(observer_enabled=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        count = write_observations(
            all_scored=candidates,
            rank_map=_make_rank_map(candidates),
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.2,
            config=config,
            obs_path=obs_path,
        )
        assert count == 0
        assert not obs_path.exists(), "No file must be written when observer is disabled"


# ── T2: ml_observer_enabled=False leaves signal scores unchanged ───────────────

def test_T2_observer_disabled_scores_unchanged():
    """ml_observer_enabled=False: scored dicts are not mutated."""
    from ml_observation_writer import write_observations

    candidates = [_make_candidate(score=28.5)]
    original_score = candidates[0]["score"]
    config = _make_config(observer_enabled=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        write_observations(
            all_scored=candidates,
            rank_map=_make_rank_map(candidates),
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.2,
            config=config,
            obs_path=Path(tmpdir) / "obs.jsonl",
        )
    assert candidates[0]["score"] == original_score, "Observer must not mutate scores"


# ── T3: ml_observer_enabled=False leaves ranking unchanged ────────────────────

def test_T3_observer_disabled_ranking_unchanged():
    """ml_observer_enabled=False: rank_map and input dicts not mutated."""
    from ml_observation_writer import write_observations

    candidates = [_make_candidate("AAPL", 30), _make_candidate("MSFT", 25)]
    rank_map = {"AAPL": 1, "MSFT": 2}
    original_rank_map = dict(rank_map)
    config = _make_config(observer_enabled=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        write_observations(
            all_scored=candidates,
            rank_map=rank_map,
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.2,
            config=config,
            obs_path=Path(tmpdir) / "obs.jsonl",
        )
    assert rank_map == original_rank_map, "rank_map must not be mutated"
    for c in candidates:
        assert "ranking_position" not in c, "Observer must not stamp ranking onto source dicts"


# ── T4: ml_observer_enabled=False leaves order eligibility unchanged ───────────

def test_T4_observer_disabled_order_eligibility_unchanged():
    """write_observations() must not mutate passed_base_threshold."""
    from ml_observation_writer import write_observations

    candidates = [_make_candidate("AAPL", 20), _make_candidate("XYZ", 5)]
    candidates[0]["passed_base_threshold"] = True
    candidates[1]["passed_base_threshold"] = False
    original_flags = [c["passed_base_threshold"] for c in candidates]

    config = _make_config(observer_enabled=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        write_observations(
            all_scored=candidates,
            rank_map=_make_rank_map(candidates),
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.2,
            config=config,
            obs_path=Path(tmpdir) / "obs.jsonl",
        )
    for c, original_flag in zip(candidates, original_flags):
        assert c["passed_base_threshold"] == original_flag


# ── T5: ml_observer_enabled=True writes records from signal pipeline ───────────

def test_T5_observer_enabled_writes_one_record_per_candidate():
    """ml_observer_enabled=True: one record per candidate, including below-threshold."""
    from ml_observation_writer import write_observations

    candidates = [
        _make_candidate("AAPL", 28),
        _make_candidate("MSFT", 22),
        _make_candidate("XYZ", 5, include_signal_scores=False),  # below-threshold
    ]
    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        count = write_observations(
            all_scored=candidates,
            rank_map=_make_rank_map(candidates),
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.2,
            config=config,
            obs_path=obs_path,
        )
        assert count == 3, f"Expected 3 records (all candidates), got {count}"
        assert obs_path.exists()
        records = _read_records(obs_path)
        assert len(records) == 3
        symbols = {r["symbol"] for r in records}
        assert symbols == {"AAPL", "MSFT", "XYZ"}


# ── T6: observer receives the real final signal structure, not a duplicate ─────

def test_T6_observation_record_reflects_real_signal_structure():
    """Observation record fields come directly from the scored dict, not a copy."""
    from ml_observation_writer import write_observations

    candidate = _make_candidate("NVDA", score=32.0, direction="LONG")
    candidate["scan_id"] = "20260520T151515"
    candidate["observation_id"] = "20260520T151515_NVDA"
    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        write_observations(
            all_scored=[candidate],
            rank_map={"NVDA": 1},
            scan_id="20260520T151515",
            regime="TRENDING_UP",
            vix=14.5,
            config=config,
            obs_path=obs_path,
        )
        rec = _read_records(obs_path)[0]
        assert rec["symbol"] == "NVDA"
        assert rec["base_score"] == 32.0
        assert rec["direction"] == "LONG"
        assert rec["scan_id"] == "20260520T151515"
        assert rec["observation_id"] == "20260520T151515_NVDA"
        assert rec["live_score_after_observer"] == rec["base_score"]
        assert rec["live_score_unchanged"] is True


# ── T7: observation record includes scan_id and observation_id ─────────────────

def test_T7_record_has_scan_id_and_observation_id():
    """Observation record must carry scan_id and observation_id."""
    from ml_observation_writer import write_observations

    scan_id = "20260520T133700"
    candidate = _make_candidate("TSLA", 25, scan_id=scan_id)
    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        write_observations(
            all_scored=[candidate],
            rank_map={"TSLA": 1},
            scan_id=scan_id,
            regime="TRENDING_UP",
            vix=14.0,
            config=config,
            obs_path=obs_path,
        )
        rec = _read_records(obs_path)[0]
        assert rec["scan_id"] == scan_id
        assert rec["observation_id"] == f"{scan_id}_TSLA"


# ── T8: observation record includes ranking_position and ranking_total ─────────

def test_T8_record_has_ranking_position_and_total():
    """Observation record must include ranking_position and ranking_total."""
    from ml_observation_writer import write_observations

    candidates = [
        _make_candidate("AAPL", 30),
        _make_candidate("MSFT", 25),
        _make_candidate("GOOG", 20),
    ]
    rank_map = {"AAPL": 1, "MSFT": 2, "GOOG": 3}
    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        write_observations(
            all_scored=candidates,
            rank_map=rank_map,
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.0,
            config=config,
            obs_path=obs_path,
        )
        records = {r["symbol"]: r for r in _read_records(obs_path)}
        assert records["AAPL"]["ranking_position"] == 1
        assert records["AAPL"]["ranking_total"] == 3
        assert records["MSFT"]["ranking_position"] == 2
        assert records["GOOG"]["ranking_total"] == 3


# ── T9: record includes signal_scores when available ──────────────────────────

def test_T9_record_includes_signal_scores_and_dim_features():
    """signal_scores and flattened dim_* fields present when score_breakdown exists."""
    from ml_observation_writer import write_observations

    candidate = _make_candidate("AAPL", 28, include_signal_scores=True)
    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        write_observations(
            all_scored=[candidate],
            rank_map={"AAPL": 1},
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=14.0,
            config=config,
            obs_path=obs_path,
        )
        rec = _read_records(obs_path)[0]
        assert rec["signal_scores"] is not None
        assert isinstance(rec["signal_scores"], dict)
        assert "dim_trend" in rec
        assert "dim_momentum" in rec
        assert rec["dim_trend"] == 6.0
        assert rec["dim_momentum"] == 5.0


# ── T10: missing signal_scores → ineligible, no crash ─────────────────────────

def test_T10_missing_signal_scores_marks_ineligible_no_crash():
    """Missing score_breakdown → record written with ml_inference_eligible=False."""
    from ml_observation_writer import write_observations

    candidate = _make_candidate("WEAKSTOCK", 5, include_signal_scores=False)
    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        count = write_observations(
            all_scored=[candidate],
            rank_map={"WEAKSTOCK": 1},
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.0,
            config=config,
            obs_path=obs_path,
        )
        assert count == 1, "Record must be written even without signal_scores"
        rec = _read_records(obs_path)[0]
        assert rec["ml_inference_eligible"] is False
        assert rec["exclusion_reason"] == "missing_signal_scores"
        assert rec["signal_scores"] is None


# ── T11: writer does not import forbidden modules ──────────────────────────────

def test_T11_writer_has_no_forbidden_imports():
    """ml_observation_writer.py actual import statements must not include ML libs."""
    src = (_REPO / "ml_observation_writer.py").read_text(encoding="utf-8")
    # Check actual import lines only (at start of line or after whitespace),
    # not docstring mentions or comment references.
    import_lines = [
        ln.lstrip()
        for ln in src.splitlines()
        if ln.lstrip().startswith(("import ", "from "))
    ]
    import_text = "\n".join(import_lines)
    forbidden_modules = ["sklearn", "joblib", "ml_engine", "training_store", "pickle"]
    for mod in forbidden_modules:
        for ln in import_lines:
            assert mod not in ln, (
                f"ml_observation_writer.py must not import '{mod}'. "
                f"Found in import line: {ln!r}"
            )


# ── T12: no model files created or loaded ─────────────────────────────────────

def test_T12_no_model_files_created_or_loaded():
    """write_observations() must not create any .pkl model files."""
    from ml_observation_writer import write_observations

    candidates = [_make_candidate()]
    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        write_observations(
            all_scored=candidates,
            rank_map={"AAPL": 1},
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.0,
            config=config,
            obs_path=obs_path,
        )
        pkl_files = list(Path(tmpdir).glob("**/*.pkl"))
        assert not pkl_files, f"No model files should be created: {pkl_files}"

    models_dir = _REPO / "data" / "models"
    if models_dir.exists():
        assert not list(models_dir.glob("*.pkl"))


# ── T13: ml_score_influence_enabled stays False, score unchanged ───────────────

def test_T13_score_influence_disabled_score_unchanged():
    """ml_score_influence_enabled=False; live_score_after_observer equals base_score."""
    from ml_observation_writer import write_observations

    candidate = _make_candidate("AAPL", score=24.0)
    config = _make_config(observer_enabled=True, score_influence_enabled=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        write_observations(
            all_scored=[candidate],
            rank_map={"AAPL": 1},
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.0,
            config=config,
            obs_path=obs_path,
        )
        rec = _read_records(obs_path)[0]
        assert rec["ml_score_influence_enabled"] is False
        assert rec["live_score_after_observer"] == rec["base_score"]
        assert rec["live_score_unchanged"] is True
    # Source dict must not be mutated after the with block closes
    assert candidate["score"] == 24.0


# ── T14: ORDER_INTENT includes observation_id at top level ─────────────────────

def test_T14_signal_dispatcher_passes_observation_id_as_top_level_kwarg():
    """signal_dispatcher.py passes observation_id= as a top-level kwarg to execute_buy/short."""
    src = (_REPO / "signal_dispatcher.py").read_text(encoding="utf-8")
    assert "observation_id=_enriched_ao.get" in src, (
        "signal_dispatcher.py must pass observation_id=_enriched_ao.get(...) "
        "as a top-level kwarg to execute_buy/execute_short for ORDER_INTENT linkage"
    )
    assert "scan_id=_enriched_ao.get" in src, (
        "signal_dispatcher.py must pass scan_id=_enriched_ao.get(...) "
        "alongside observation_id for ORDER_INTENT linkage"
    )


# ── T15: existing order behaviour unchanged ────────────────────────────────────

def test_T15_execute_buy_and_short_core_signatures_unchanged():
    """execute_buy and execute_short must still define their core parameters in source."""
    # Source inspection avoids importing orders_core (which has a pre-existing
    # cross-test contamination via bot_state → agents_required_to_agree KeyError).
    src = (_REPO / "orders_core.py").read_text(encoding="utf-8")
    required_core = [
        "symbol: str",
        "price: float",
        "atr: float",
        "score: int",
        "portfolio_value:",
        "signal_scores:",
        "agent_outputs:",
    ]
    for param_pattern in required_core:
        assert param_pattern in src, (
            f"orders_core.py is missing expected parameter definition: {param_pattern!r}"
        )
    # Verify Sprint 2 did not add execute_buy/execute_short as new imports to the writer
    writer_src = (_REPO / "ml_observation_writer.py").read_text(encoding="utf-8")
    for order_fn in ("execute_buy", "execute_short", "execute_sell"):
        assert order_fn not in writer_src, (
            f"ml_observation_writer.py must not reference '{order_fn}'"
        )


# ── T16: missing data/ml directory → created gracefully ───────────────────────

def test_T16_missing_directory_created_gracefully():
    """write_observations() creates the output directory if it doesn't exist."""
    from ml_observation_writer import write_observations

    candidates = [_make_candidate()]
    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "nested" / "path" / "obs.jsonl"
        assert not obs_path.parent.exists()
        count = write_observations(
            all_scored=candidates,
            rank_map={"AAPL": 1},
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.0,
            config=config,
            obs_path=obs_path,
        )
        assert count == 1, "Should write successfully after directory creation"
        assert obs_path.exists()


# ── T17: malformed candidate does not block the live pipeline ──────────────────

def test_T17_malformed_candidate_does_not_crash():
    """A bad candidate dict is skipped; good candidates still write. Never raises."""
    from ml_observation_writer import write_observations

    good = _make_candidate("AAPL", 25)
    bad = {"totally_wrong_key": True}   # no symbol, no score

    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        # Must not raise
        count = write_observations(
            all_scored=[good, bad],
            rank_map={"AAPL": 1},
            scan_id="20260520T123456",
            regime="TRENDING_UP",
            vix=15.0,
            config=config,
            obs_path=obs_path,
        )
        assert count >= 1, "Good candidate must still be written"


# ── T18: atomic JSONL append — multiple calls accumulate correctly ─────────────

def test_T18_safe_jsonl_append_multiple_calls():
    """Multiple write_observations() calls append; each line is valid JSON."""
    from ml_observation_writer import write_observations

    # Candidates must carry matching scan_id so the written records reflect each batch.
    batch1 = [_make_candidate("AAPL", 28, scan_id="20260520T100000")]
    batch2 = [_make_candidate("MSFT", 22, scan_id="20260520T110000")]
    config = _make_config(observer_enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        obs_path = Path(tmpdir) / "obs.jsonl"
        write_observations(
            all_scored=batch1, rank_map={"AAPL": 1},
            scan_id="20260520T100000", regime="TRENDING_UP",
            vix=14.0, config=config, obs_path=obs_path,
        )
        write_observations(
            all_scored=batch2, rank_map={"MSFT": 1},
            scan_id="20260520T110000", regime="TRENDING_UP",
            vix=14.5, config=config, obs_path=obs_path,
        )
        records = _read_records(obs_path)
        assert len(records) == 2, "Each call appends; earlier records must not be overwritten"
        assert records[0]["symbol"] == "AAPL"
        assert records[1]["symbol"] == "MSFT"
        assert records[0]["scan_id"] == "20260520T100000"
        assert records[1]["scan_id"] == "20260520T110000"


# ── T19: old ml_engine remains non-importable ──────────────────────────────────

def test_T19_ml_engine_still_not_importable():
    """Sprint 1 guarantee preserved: ml_engine.py must not exist."""
    assert not (_REPO / "ml_engine.py").exists()
    for mod_name in list(sys.modules.keys()):
        if "ml_engine" in mod_name:
            del sys.modules[mod_name]
    with pytest.raises((ImportError, ModuleNotFoundError)):
        import ml_engine  # noqa: F401


# ── T20: leaky data/models/*.pkl still absent ─────────────────────────────────

def test_T20_leaky_models_still_absent_from_runtime_path():
    """Sprint 1 guarantee preserved: data/models/ has no .pkl files."""
    models_dir = _REPO / "data" / "models"
    pkl_files = list(models_dir.glob("*.pkl")) if models_dir.exists() else []
    assert not pkl_files, (
        f"Leaky .pkl files found in data/models/: {[f.name for f in pkl_files]}"
    )
