# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  tests/test_ml_sprint37_source_accuracy.py  ║
# ║   Sprint 3.7 — Candidate source accuracy + canary baseline   ║
# ║                                                              ║
# ║   T1–T6   : SignalPipelineResult exposes rank_map/total/vix  ║
# ║   T7–T10  : write_observations moved to bot_trading.py       ║
# ║   T11–T13 : schema version = sprint37_v1                     ║
# ║   T14–T16 : --since-scan-id canary baseline                  ║
# ╚══════════════════════════════════════════════════════════════╝
"""
Sprint 3.7 regression tests.

Guarantees:
  - SignalPipelineResult exposes rank_map, ranking_total, vix
  - write_observations() is NOT called inside run_signal_pipeline()
  - write_observations() IS called in bot_trading.py after handoff enrichment
  - candidate_source is "handoff_reader" in observations when handoff is active
  - schema_version = "sprint37_v1" on new records
  - --since-scan-id filters canary duplicate check without hiding integrity failures

No model training. No model loading. No prediction. No score changes.
No ranking changes. No order eligibility changes. No sizing changes.
No execution changes.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── Path wiring ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load_health_check():
    """Load scripts/ml_observation_health_check.py from the scripts/ directory."""
    spec = importlib.util.spec_from_file_location(
        "ml_observation_health_check",
        REPO_ROOT / "scripts" / "ml_observation_health_check.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_obs_record(
    scan_id: str = "20260521T100000",
    obs_id_suffix: str = "AAPL",
    candidate_source: str = "scanner",
    schema_version: str = "sprint37_v1",
    duplicate: bool = False,
) -> dict:
    return {
        "schema_version": schema_version,
        "timestamp_utc": "2026-05-21T10:00:00+00:00",
        "session_date": "2026-05-21",
        "scan_id": scan_id,
        "observation_id": f"{scan_id}_{obs_id_suffix}" + ("_dup" if duplicate else ""),
        "symbol": obs_id_suffix,
        "direction": "LONG",
        "candidate_source": candidate_source,
        "base_score": 42.0,
        "live_score_after_observer": 42.0,
        "live_score_unchanged": True,
        "ranking_position": 1,
        "ranking_total": 10,
        "signal_scores": {"momentum": 0.7},
        "regime": "BULL_TRENDING",
        "vix": 18.0,
        "time_of_day": "10:00",
        "day_of_week": "Thursday",
        "is_after_hours": False,
        "passed_base_threshold": True,
        "ml_observer_enabled": True,
        "ml_score_influence_enabled": False,
        "ml_inference_eligible": False,
        "exclusion_reason": "prediction_not_implemented_sprint_2",
        "order_intent_linked": False,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# T1–T3: SignalPipelineResult exposes rank_map, ranking_total, vix
# ══════════════════════════════════════════════════════════════════════════════

class TestSignalPipelineResultFields:
    """SignalPipelineResult must expose rank_map, ranking_total, vix."""

    def _result_class(self):
        from signal_pipeline import SignalPipelineResult
        return SignalPipelineResult

    def test_T1_result_has_rank_map_field(self):
        """T1: SignalPipelineResult has a rank_map field defaulting to {}."""
        SPR = self._result_class()
        r = SPR(
            signals=[], scored=[], all_scored=[], news_sentiment={},
            universe=[], regime_name="BULL_TRENDING",
        )
        assert hasattr(r, "rank_map"), "SignalPipelineResult missing rank_map field"
        assert isinstance(r.rank_map, dict)

    def test_T2_result_has_ranking_total_field(self):
        """T2: SignalPipelineResult has a ranking_total field defaulting to 0."""
        SPR = self._result_class()
        r = SPR(
            signals=[], scored=[], all_scored=[], news_sentiment={},
            universe=[], regime_name="BULL_TRENDING",
        )
        assert hasattr(r, "ranking_total"), "SignalPipelineResult missing ranking_total field"
        assert r.ranking_total == 0

    def test_T3_result_has_vix_field(self):
        """T3: SignalPipelineResult has a vix field defaulting to 0.0."""
        SPR = self._result_class()
        r = SPR(
            signals=[], scored=[], all_scored=[], news_sentiment={},
            universe=[], regime_name="BULL_TRENDING",
        )
        assert hasattr(r, "vix"), "SignalPipelineResult missing vix field"
        assert r.vix == 0.0

    def test_T4_result_rank_map_accepts_non_empty_dict(self):
        """T4: rank_map can be set to a populated dict."""
        SPR = self._result_class()
        rm = {"AAPL": 1, "MSFT": 2}
        r = SPR(
            signals=[], scored=[], all_scored=[], news_sentiment={},
            universe=[], regime_name="BULL_TRENDING",
            rank_map=rm, ranking_total=2, vix=18.5,
        )
        assert r.rank_map == rm
        assert r.ranking_total == 2
        assert r.vix == 18.5


# ══════════════════════════════════════════════════════════════════════════════
# T5–T6: write_observations NOT in signal_pipeline, IS in bot_trading
# ══════════════════════════════════════════════════════════════════════════════

class TestObservationWriterLocation:
    """write_observations must be called in bot_trading, not signal_pipeline."""

    def test_T5_signal_pipeline_does_not_call_write_observations(self):
        """T5: run_signal_pipeline() source does not contain a write_observations call."""
        sp_path = REPO_ROOT / "signal_pipeline.py"
        source = sp_path.read_text(encoding="utf-8")
        # The function body of run_signal_pipeline must not call write_observations.
        # We check for the call pattern; the import may still be in the file from old code.
        # The key invariant: write_observations( must not appear inside the function.
        import ast
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_signal_pipeline":
                func_source = ast.unparse(node)
                assert "write_observations(" not in func_source, (
                    "run_signal_pipeline() must NOT call write_observations() — "
                    "Sprint 3.7 moved this call to bot_trading.py"
                )
                return
        pytest.fail("run_signal_pipeline() function not found in signal_pipeline.py")

    def test_T6_bot_trading_calls_write_observations_after_handoff(self):
        """T6: bot_trading.py source contains write_observations call after handoff enrichment."""
        bt_path = REPO_ROOT / "bot_trading.py"
        source = bt_path.read_text(encoding="utf-8")
        assert "write_observations" in source, (
            "bot_trading.py must contain a write_observations call (Sprint 3.7)"
        )
        # The call must appear AFTER the handoff_reader promotion block.
        # Search for the candidate_source promotion assignment (the last statement in the block).
        handoff_pos = source.find('candidate_source"] = "handoff_reader"')
        write_obs_pos = source.find("write_observations")
        assert handoff_pos != -1, (
            'candidate_source"] = "handoff_reader" promotion not found in bot_trading.py'
        )
        assert write_obs_pos > handoff_pos, (
            "write_observations call must appear AFTER handoff candidate_source promotion"
        )


# ══════════════════════════════════════════════════════════════════════════════
# T7–T9: candidate_source accuracy via ml_observation_writer
# ══════════════════════════════════════════════════════════════════════════════

class TestCandidateSourceInObservations:
    """Observations must reflect post-handoff-enrichment candidate_source values."""

    def _make_scored(self, candidate_source: str) -> dict:
        return {
            "symbol": "AAPL",
            "score": 42.0,
            "direction": "LONG",
            "score_breakdown": {"momentum": 0.7},
            "scan_id": "20260521T100000",
            "observation_id": "20260521T100000_AAPL",
            "session_date": "2026-05-21",
            "passed_base_threshold": True,
            "ranking_position": 1,
            "ranking_total": 5,
            "candidate_source": candidate_source,
        }

    def test_T7_handoff_source_written_to_observation(self, tmp_path):
        """T7: When candidate_source=handoff_reader, observation records it correctly."""
        from ml_observation_writer import write_observations
        scored = [self._make_scored("handoff_reader")]
        obs_file = tmp_path / "ml_observations.jsonl"
        write_observations(
            all_scored=scored,
            rank_map={"AAPL": 1},
            scan_id="20260521T100000",
            regime="BULL_TRENDING",
            vix=18.0,
            config={"ml_observer_enabled": True, "ml_data_dir": "data/ml"},
            obs_path=obs_file,
        )
        records = [json.loads(l) for l in obs_file.read_text().splitlines()]
        assert len(records) == 1
        assert records[0]["candidate_source"] == "handoff_reader"

    def test_T8_scanner_source_written_when_no_handoff(self, tmp_path):
        """T8: When candidate_source=scanner, observation records it as scanner."""
        from ml_observation_writer import write_observations
        scored = [self._make_scored("scanner")]
        obs_file = tmp_path / "ml_observations.jsonl"
        write_observations(
            all_scored=scored,
            rank_map={"AAPL": 1},
            scan_id="20260521T100001",
            regime="BULL_TRENDING",
            vix=18.0,
            config={"ml_observer_enabled": True, "ml_data_dir": "data/ml"},
            obs_path=obs_file,
        )
        records = [json.loads(l) for l in obs_file.read_text().splitlines()]
        assert records[0]["candidate_source"] == "scanner"

    def test_T9_pipeline_vix_available_to_writer(self):
        """T9: pipeline.vix is accessible as a float so bot_trading can pass it to writer."""
        from signal_pipeline import SignalPipelineResult
        r = SignalPipelineResult(
            signals=[], scored=[], all_scored=[], news_sentiment={},
            universe=[], regime_name="BULL_TRENDING", vix=21.3,
        )
        assert isinstance(r.vix, float)
        assert r.vix == 21.3


# ══════════════════════════════════════════════════════════════════════════════
# T10–T11: schema version = sprint37_v1
# ══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersionSprint37:
    """New observations must carry schema_version=sprint37_v1."""

    def test_T10_schema_version_is_sprint37(self, tmp_path):
        """T10: write_observations produces records with schema_version=sprint37_v1."""
        from ml_observation_writer import write_observations
        scored = [{
            "symbol": "NVDA",
            "score": 50.0,
            "direction": "LONG",
            "score_breakdown": {"momentum": 0.8},
            "scan_id": "20260521T110000",
            "observation_id": "20260521T110000_NVDA",
            "session_date": "2026-05-21",
            "passed_base_threshold": True,
            "ranking_position": 1,
            "ranking_total": 3,
            "candidate_source": "scanner",
        }]
        obs_file = tmp_path / "ml_observations.jsonl"
        write_observations(
            all_scored=scored,
            rank_map={"NVDA": 1},
            scan_id="20260521T110000",
            regime="BULL_TRENDING",
            vix=17.0,
            config={"ml_observer_enabled": True, "ml_data_dir": "data/ml"},
            obs_path=obs_file,
        )
        records = [json.loads(l) for l in obs_file.read_text().splitlines()]
        assert records[0]["schema_version"] == "sprint37_v1"

    def test_T11_schema_version_constant_is_sprint37(self):
        """T11: SCHEMA_VERSION constant in ml_observation_writer is sprint37_v1."""
        import ml_observation_writer
        assert ml_observation_writer.SCHEMA_VERSION == "sprint37_v1", (
            f"Expected sprint37_v1, got {ml_observation_writer.SCHEMA_VERSION}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# T12–T16: --since-scan-id canary baseline
# ══════════════════════════════════════════════════════════════════════════════

class TestCanaryBaseline:
    """since_scan_id filters duplicate check without hiding integrity failures."""

    def _hc(self):
        return _load_health_check()

    def test_T12_canary_fails_without_baseline_for_historical_duplicate(self, tmp_path):
        """T12: Without since_scan_id, a historical duplicate causes canary FAIL."""
        hc = self._hc()
        obs_file = tmp_path / "ml_observations.jsonl"
        # Two records with SAME observation_id in same scan_id = duplicate
        records = [
            _make_obs_record(scan_id="20260520T133247", obs_id_suffix="AAPL"),
            _make_obs_record(scan_id="20260520T133247", obs_id_suffix="AAPL"),
        ]
        _write_jsonl(obs_file, records)
        passed, failures = hc.run_canary_validation(obs_path=obs_file)
        assert not passed, "Expected FAIL due to historical duplicate"
        assert any("duplicate" in f for f in failures)

    def test_T13_canary_passes_with_baseline_that_skips_historical_duplicate(self, tmp_path):
        """T13: With since_scan_id after the historical artifact, canary passes."""
        hc = self._hc()
        obs_file = tmp_path / "ml_observations.jsonl"
        # Historical duplicate in an old scan
        records = [
            _make_obs_record(scan_id="20260520T133247", obs_id_suffix="AAPL"),
            _make_obs_record(scan_id="20260520T133247", obs_id_suffix="AAPL"),
        ]
        _write_jsonl(obs_file, records)
        # Baseline AFTER the historical artifact — no new duplicates in scope
        passed, failures = hc.run_canary_validation(
            obs_path=obs_file, since_scan_id="20260521T000000"
        )
        assert passed, f"Expected PASS with baseline skipping historical duplicate, got: {failures}"

    def test_T14_canary_still_catches_new_duplicates_after_baseline(self, tmp_path):
        """T14: since_scan_id does NOT suppress duplicates in post-baseline scans."""
        hc = self._hc()
        obs_file = tmp_path / "ml_observations.jsonl"
        # Historical duplicate (old scan) + new duplicate (new scan)
        records = [
            _make_obs_record(scan_id="20260520T133247", obs_id_suffix="AAPL"),
            _make_obs_record(scan_id="20260520T133247", obs_id_suffix="AAPL"),
            # New post-baseline duplicate
            _make_obs_record(scan_id="20260521T100000", obs_id_suffix="MSFT"),
            _make_obs_record(scan_id="20260521T100000", obs_id_suffix="MSFT"),
        ]
        _write_jsonl(obs_file, records)
        passed, failures = hc.run_canary_validation(
            obs_path=obs_file, since_scan_id="20260521T000000"
        )
        assert not passed, "Expected FAIL for new post-baseline duplicate"
        assert any("duplicate" in f for f in failures)

    def test_T15_since_scan_id_does_not_skip_integrity_checks(self, tmp_path):
        """T15: Integrity checks (missing fields) still run on ALL records with since_scan_id."""
        hc = self._hc()
        obs_file = tmp_path / "ml_observations.jsonl"
        # Old record (before baseline) with score mutation
        bad = _make_obs_record(scan_id="20260519T100000", obs_id_suffix="TSLA")
        bad["live_score_unchanged"] = False  # integrity violation
        records = [bad]
        _write_jsonl(obs_file, records)
        # Even with baseline set to after this record's scan, the integrity check must fire
        passed, failures = hc.run_canary_validation(
            obs_path=obs_file, since_scan_id="20260521T000000"
        )
        assert not passed, "Expected FAIL for integrity violation even with since_scan_id"
        assert any("live_score_unchanged" in f for f in failures)

    def test_T16_since_scan_id_argument_accepted_by_cli_parser(self):
        """T16: --since-scan-id argument is accepted by the argparse parser."""
        hc = self._hc()
        parser = hc._build_arg_parser()
        args = parser.parse_args(["--canary", "--since-scan-id", "20260521T000000"])
        assert args.canary is True
        assert args.since_scan_id == "20260521T000000"
