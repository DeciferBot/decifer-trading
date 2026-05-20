# tests/test_audit_trade_ledger_data_path.py
"""
Tests for scripts/audit_trade_ledger_data_path.py

Verifies:
- The audit script never imports live trading modules
- All audit functions handle missing files gracefully
- Duplicate trade ID detection
- Missing label / P&L detection
- Contamination detection (options, UNKNOWN trade_type, non-structural regimes)
- Path inconsistency detection
- Timestamp integrity detection
- Schema drift detection
- ML logic correctness checks
- JSON and markdown outputs are produced correctly

All tests use tmp_path — no real data files are touched.
"""

from __future__ import annotations

import ast
import json
import sys
import types
from pathlib import Path

import pytest

# ── Stub config so the script can be imported without the full environment ─────

_configmod = types.ModuleType("config")
_configmod.CONFIG = {
    "training_records": "/tmp/audit_test_training.jsonl",
    "trade_events_log": "/tmp/audit_test_events.jsonl",
    "trade_log": "/tmp/audit_test_trades.json",
}
sys.modules.setdefault("config", _configmod)

# ── Import the module under test ──────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import audit_trade_ledger_data_path as audit  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────

_DEFAULT_SIG = {
    "trend": 8, "momentum": 6, "squeeze": 2, "flow": 7, "breakout": 3,
    "news": 1, "social": 1, "reversion": 0, "overnight_drift": 4,
    "pead": 0, "short_squeeze": 0, "catalyst": 0, "analyst_revision": 0,
    "iv_skew": 2, "fx_macro": 0, "fx_momentum": 0, "insider_buying": 5, "mtf": 9,
}


def _make_record(
    trade_id="AAPL_001",
    symbol="AAPL",
    direction="LONG",
    trade_type="INTRADAY",
    instrument="stock",
    fill_price=100.0,
    exit_price=101.5,
    pnl=150.0,
    pnl_pct=0.015,
    hold_minutes=60.0,
    exit_reason="tp_hit",
    regime="TRENDING_UP",
    signal_scores=None,
    conviction=0.7,
    score=35.0,
    ts_fill="2026-04-01T10:30:00+00:00",
    ts_close="2026-04-01T11:30:00+00:00",
) -> dict:
    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "trade_type": trade_type,
        "instrument": instrument,
        "fill_price": fill_price,
        "exit_price": exit_price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "hold_minutes": hold_minutes,
        "exit_reason": exit_reason,
        "regime": regime,
        "signal_scores": signal_scores if signal_scores is not None else dict(_DEFAULT_SIG),
        "conviction": conviction,
        "score": score,
        "ts_fill": ts_fill,
        "ts_close": ts_close,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _write_json(path: Path, data) -> None:
    with open(path, "w") as fh:
        json.dump(data, fh)


# ── TestImportSafety ──────────────────────────────────────────────────────────

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "audit_trade_ledger_data_path.py"

_FORBIDDEN = {
    "bot", "bot_ibkr", "bot_dashboard", "bot_trading",
    "orders_core", "orders_portfolio", "orders_options", "orders_state",
    "apex_orchestrator", "market_intelligence",
    "scanner", "signals", "learning",
    "risk", "sizing",
    "ibkr_reconciler",
    "ib_async", "ib_insync",
}


def _get_imported_names() -> set[str]:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class TestImportSafety:
    def test_does_not_import_broker_modules(self):
        imported = _get_imported_names()
        broker = imported & {"ib_async", "ib_insync", "ibkr_reconciler", "bot_ibkr"}
        assert not broker, f"Script imports broker modules: {broker}"

    def test_does_not_import_orders_modules(self):
        imported = _get_imported_names()
        orders = imported & {"orders_core", "orders_portfolio", "orders_options", "orders_state"}
        assert not orders, f"Script imports order modules: {orders}"

    def test_does_not_import_apex_modules(self):
        imported = _get_imported_names()
        apex = imported & {"apex_orchestrator", "market_intelligence"}
        assert not apex, f"Script imports Apex modules: {apex}"

    def test_does_not_import_any_forbidden_module(self):
        imported = _get_imported_names()
        forbidden_found = imported & _FORBIDDEN
        assert not forbidden_found, f"Script imports forbidden modules: {forbidden_found}"


# ── TestMissingFiles ──────────────────────────────────────────────────────────

class TestMissingFiles:
    def test_load_jsonl_safe_missing(self):
        result = audit._load_jsonl_safe("/tmp/nonexistent_audit_test.jsonl")
        assert result == []

    def test_load_json_safe_missing(self):
        result = audit._load_json_safe("/tmp/nonexistent_audit_test.json")
        assert result is None

    def test_analyze_primary_ledger_missing_file(self):
        result = audit.analyze_primary_ledger("/tmp/nonexistent.jsonl", [])
        assert "error" in result or result.get("total_records") == 0

    def test_check_lifecycle_integrity_empty_events(self):
        records = [_make_record()]
        result = audit.check_lifecycle_integrity(records, [])
        assert result["training_no_event_coverage"] == 1
        assert result["training_with_intent_match"] == 0

    def test_check_label_correctness_empty(self):
        result = audit.check_label_correctness([])
        assert result["total_records"] == 0

    def test_check_contamination_no_legacy_file(self, tmp_path):
        paths = {"trades_legacy": str(tmp_path / "missing.json")}
        result = audit.check_contamination([_make_record()], paths)
        assert result["legacy_trades_json_overlap"] == 0

    def test_discover_sources_missing_data_dir(self, tmp_path):
        result = audit.discover_sources(str(tmp_path / "nonexistent"))
        assert "error" in result


# ── TestDuplicateDetection ────────────────────────────────────────────────────

class TestDuplicateDetection:
    def test_detects_duplicate_trade_ids(self):
        records = [
            _make_record(trade_id="DUP_001"),
            _make_record(trade_id="DUP_001"),
            _make_record(trade_id="UNIQUE_002"),
        ]
        result = audit.analyze_primary_ledger("/tmp/fake.jsonl", records)
        assert result["duplicate_trade_ids"] == 1
        assert "DUP_001" in result["duplicate_trade_id_list"]

    def test_clean_records_have_no_duplicates(self):
        records = [_make_record(trade_id=f"ID_{i}") for i in range(10)]
        result = audit.analyze_primary_ledger("/tmp/fake.jsonl", records)
        assert result["duplicate_trade_ids"] == 0
        assert result["duplicate_trade_id_list"] == []


# ── TestLabelDetection ────────────────────────────────────────────────────────

class TestLabelDetection:
    def test_win_record_labelled_correctly(self):
        result = audit.check_label_correctness([_make_record(pnl=150.0, pnl_pct=0.015)])
        assert result["wins"] == 1
        assert result["losses"] == 0

    def test_loss_record_labelled_correctly(self):
        result = audit.check_label_correctness([_make_record(pnl=-200.0, pnl_pct=-0.02)])
        assert result["losses"] == 1
        assert result["wins"] == 0

    def test_breakeven_record_labelled_correctly(self):
        result = audit.check_label_correctness([_make_record(pnl=0.05, pnl_pct=0.00005)])
        assert result["breakevens"] == 1

    def test_detects_null_pnl(self):
        records = [_make_record(pnl=None)]
        for r in records:
            r["pnl"] = None
        result = audit.check_label_correctness(records)
        assert result["unlabellable_null_pnl"] == 1

    def test_win_loss_breakeven_counts_sum_to_labellable(self):
        records = [
            _make_record(pnl=100.0),
            _make_record(pnl=-50.0, trade_id="B"),
            _make_record(pnl=0.01, pnl_pct=0.0001, trade_id="C"),
        ]
        result = audit.check_label_correctness(records)
        assert result["wins"] + result["losses"] + result["breakevens"] == result["labellable_records"]

    def test_label_inversion_risk_flagged_when_win_rate_low(self):
        records = [_make_record(pnl=-100.0, trade_id=f"L{i}") for i in range(10)]
        result = audit.check_label_correctness(records)
        assert result["label_inversion_risk"] is True

    def test_no_inversion_risk_at_reasonable_win_rate(self):
        records = (
            [_make_record(pnl=100.0, trade_id=f"W{i}") for i in range(6)]
            + [_make_record(pnl=-50.0, trade_id=f"L{i}") for i in range(4)]
        )
        result = audit.check_label_correctness(records)
        assert result["label_inversion_risk"] is False


# ── TestContaminationDetection ────────────────────────────────────────────────

class TestContaminationDetection:
    def test_detects_options_records(self, tmp_path):
        records = [
            _make_record(instrument="stock"),
            _make_record(trade_id="OPT", instrument="options_call"),
        ]
        paths = {"trades_legacy": str(tmp_path / "missing.json")}
        result = audit.check_contamination(records, paths)
        assert result["options_records"] == 1

    def test_detects_unknown_trade_type(self, tmp_path):
        records = [
            _make_record(trade_type="INTRADAY"),
            _make_record(trade_id="UNK", trade_type="UNKNOWN"),
        ]
        paths = {"trades_legacy": str(tmp_path / "missing.json")}
        result = audit.check_contamination(records, paths)
        assert result["unknown_trade_type"] == 1

    def test_detects_non_structural_regime_labels(self, tmp_path):
        records = [
            _make_record(regime="TRENDING_UP"),
            _make_record(trade_id="SESS", regime="FEAR_ELEVATED"),
        ]
        paths = {"trades_legacy": str(tmp_path / "missing.json")}
        result = audit.check_contamination(records, paths)
        assert result["session_character_regime_records"] == 1
        assert "FEAR_ELEVATED" in result["session_character_regimes_found"]

    def test_no_contamination_in_clean_records(self, tmp_path):
        records = [_make_record(trade_id=f"T{i}") for i in range(5)]
        paths = {"trades_legacy": str(tmp_path / "missing.json")}
        result = audit.check_contamination(records, paths)
        assert result["options_records"] == 0
        assert result["unknown_trade_type"] == 0
        assert result["session_character_regime_records"] == 0

    def test_detects_equity_label_inconsistency(self, tmp_path):
        records = [
            _make_record(instrument="stock"),
            _make_record(trade_id="EQ2", instrument="equity_long"),
        ]
        paths = {"trades_legacy": str(tmp_path / "missing.json")}
        result = audit.check_contamination(records, paths)
        assert result["equity_label_normalisation_issue"] is True

    def test_detects_legacy_trades_overlap(self, tmp_path):
        legacy_path = tmp_path / "trades.json"
        legacy_records = [{"trade_id": "SHARED_001", "symbol": "AAPL"}]
        _write_json(legacy_path, legacy_records)
        training_records = [_make_record(trade_id="SHARED_001")]
        paths = {"trades_legacy": str(legacy_path)}
        result = audit.check_contamination(training_records, paths)
        assert result["legacy_trades_json_overlap"] == 1


# ── TestPathConsistency ───────────────────────────────────────────────────────

class TestPathConsistency:
    def test_detects_hardcoded_path_mismatch(self, tmp_path):
        # Create a fake source file with a hardcoded path
        fake_src = tmp_path / "fake_reader.py"
        fake_src.write_text('with open("data/trades.json") as f: pass\n')
        # grep it directly
        result = audit._grep_file_for_patterns(str(fake_src), ["trades.json", "training_store"])
        assert len(result["trades.json"]) > 0
        assert len(result["training_store"]) == 0

    def test_no_inconsistency_in_canonical_file(self, tmp_path):
        good_src = tmp_path / "good_reader.py"
        good_src.write_text("import training_store\nrecords = training_store.load()\n")
        result = audit._grep_file_for_patterns(str(good_src), ["trades.json", "training_store"])
        assert len(result["trades.json"]) == 0
        assert len(result["training_store"]) > 0


# ── TestTimestampIntegrity ─────────────────────────────────────────────────────

class TestTimestampIntegrity:
    def test_detects_exit_before_entry(self):
        records = [_make_record(
            ts_fill="2026-04-01T11:30:00+00:00",
            ts_close="2026-04-01T10:00:00+00:00",  # close BEFORE fill
        )]
        result = audit.check_feature_time_integrity(records)
        assert result["timestamp_violations_close_before_fill"] == 1

    def test_valid_timestamps_no_violation(self):
        records = [_make_record(
            ts_fill="2026-04-01T10:00:00+00:00",
            ts_close="2026-04-01T11:00:00+00:00",
        )]
        result = audit.check_feature_time_integrity(records)
        assert result["timestamp_violations_close_before_fill"] == 0

    def test_detects_zero_hold_minutes(self):
        records = [_make_record(hold_minutes=0.0)]
        result = audit.check_feature_time_integrity(records)
        assert result["hold_minutes_zero_or_negative"] == 1

    def test_detects_unparseable_timestamps(self):
        r = _make_record()
        r["ts_fill"] = "NOT_A_DATE"
        result = audit.check_feature_time_integrity([r])
        assert result["timestamp_unparseable"] >= 1


# ── TestSchemaDrift ───────────────────────────────────────────────────────────

class TestSchemaDrift:
    def test_detects_records_missing_signal_scores(self):
        records = [
            _make_record(),  # has signal_scores
            _make_record(trade_id="NOSIG", signal_scores={}),  # empty
        ]
        result = audit.analyze_primary_ledger("/tmp/fake.jsonl", records)
        assert result["without_signal_scores"] == 1

    def test_identifies_pre_migration_generation(self):
        # Pre-migration: no pnl_pct
        r = _make_record()
        del r["pnl_pct"]
        r2 = _make_record(trade_id="B")  # has pnl_pct
        result = audit.check_schema_consistency([r, r2])
        # Gen3 (pnl_pct present) should cover only 1 record
        gen3 = result["schema_generations"]["gen3_post_migration_fields"]["record_count"]
        assert gen3 == 1

    def test_all_fields_present_returns_100pct(self):
        records = [_make_record(trade_id=f"T{i}") for i in range(5)]
        result = audit.check_schema_consistency(records)
        # trade_id should be 100% present
        assert result["field_presence"]["trade_id"]["pct_present"] == 100.0


# ── TestMLLogicCorrectness ────────────────────────────────────────────────────

class TestMLLogicCorrectness:
    def test_ml_logic_verdict_key_present_in_output(self, tmp_path):
        """After Sprint 1 removal, verdict must be ML_ENGINE_REMOVED."""
        paths = {
            "training_records": str(tmp_path / "tr.jsonl"),
            "trade_events": str(tmp_path / "ev.jsonl"),
            "trades_legacy": str(tmp_path / "tl.json"),
            "models_dir": str(tmp_path / "models"),
            "ml_engine_src": str(audit._REPO / "ml_engine.py"),
            "config_src": str(audit._REPO / "config.py"),
            "data_dir": str(tmp_path),
            "audit_json": str(tmp_path / "audit.json"),
            "audit_md": str(tmp_path / "audit.md"),
        }
        result = audit.audit_ml_logic_correctness(paths)
        assert "ml_logic_verdict" in result
        assert result["ml_logic_verdict"] in {
            "ML_ENGINE_REMOVED",
            "ML LOGIC CORRECT, DATA/SIGNAL WEAK",
            "ML LOGIC NEEDS FIXES BEFORE TRUSTING RESULTS",
            "ML LOGIC INVALID",
        }

    def test_ml_engine_file_deleted(self):
        """ml_engine.py must not exist — Sprint 1 clean removal proof."""
        assert not (audit._REPO / "ml_engine.py").exists(), (
            "ml_engine.py still exists. Sprint 1 requires full deletion of the legacy ML engine."
        )

    def test_leaky_models_quarantined_not_in_runtime_path(self):
        """data/models/ must have no .pkl files — leaky models quarantined."""
        models_dir = audit._REPO / "data" / "models"
        pkl_files = list(models_dir.glob("*.pkl")) if models_dir.exists() else []
        assert not pkl_files, (
            f"Found .pkl files in data/models/: {[f.name for f in pkl_files]}. "
            "These models contain holding_minutes leakage and must be quarantined."
        )

    def test_holding_minutes_not_in_feature_cols(self):
        """After removal, ml_engine.py is gone — no feature_cols to check."""
        ml_engine_path = audit._REPO / "ml_engine.py"
        if not ml_engine_path.exists():
            return  # ml_engine.py deleted — no feature cols to inspect
        ml_src = audit._read_source_text(str(ml_engine_path))
        feature_section = audit._extract_feature_cols_section(ml_src)
        assert "holding_minutes" not in feature_section

    def test_no_random_train_test_split(self):
        """After removal, ml_engine.py is gone — no random split to check."""
        ml_engine_path = audit._REPO / "ml_engine.py"
        if not ml_engine_path.exists():
            return  # ml_engine.py deleted — nothing to check
        ml_src = audit._read_source_text(str(ml_engine_path))
        assert "train_test_split" not in ml_src

    def test_ml_enabled_default_flagged(self):
        paths = {
            "config_src": str(audit._REPO / "config.py"),
            "ml_engine_src": str(audit._REPO / "ml_engine.py"),
            "models_dir": str(audit._REPO / "data" / "models"),
        }
        result = audit.audit_ml_logic_correctness(paths)
        ai = result.get("apex_integration_safety", {})
        if ai.get("ml_enabled_default_true_in_config"):
            assert "RISK" in ai.get("ml_enabled_risk", "")

    def test_inverted_auc_check_runs_without_raising(self, tmp_path):
        paths = {
            "training_records": str(tmp_path / "tr.jsonl"),
            "models_dir": str(tmp_path / "models"),
            "ml_engine_src": str(audit._REPO / "ml_engine.py"),
            "config_src": str(audit._REPO / "config.py"),
        }
        try:
            result = audit.audit_ml_logic_correctness(paths)
            ev = result.get("model_evaluation", {})
            assert ev.get("status") in ("SKIPPED", "OK", "ERROR")
        except Exception as exc:
            pytest.fail(f"audit_ml_logic_correctness raised unexpectedly: {exc}")

    def test_audit_returns_removed_verdict_when_ml_engine_absent(self, tmp_path):
        """When ml_engine.py is absent, verdict must be ML_ENGINE_REMOVED."""
        paths = {
            "training_records": str(tmp_path / "tr.jsonl"),
            "models_dir": str(tmp_path / "models"),
            "ml_engine_src": str(tmp_path / "ml_engine.py"),  # points to non-existent path
            "config_src": str(audit._REPO / "config.py"),
        }
        result = audit.audit_ml_logic_correctness(paths)
        assert result["ml_logic_verdict"] == "ML_ENGINE_REMOVED"


# ── TestOutputs ───────────────────────────────────────────────────────────────

class TestOutputs:
    def _run_audit(self, tmp_path: Path) -> dict:
        records = [_make_record(trade_id=f"T{i}") for i in range(10)]
        tr_path = tmp_path / "training_records.jsonl"
        ev_path = tmp_path / "events.jsonl"
        _write_jsonl(tr_path, records)

        paths = {
            "data_dir": str(tmp_path),
            "training_records": str(tr_path),
            "trade_events": str(ev_path),
            "trades_legacy": str(tmp_path / "missing.json"),
            "models_dir": str(tmp_path / "models"),
            "ml_engine_src": str(audit._REPO / "ml_engine.py"),
            "config_src": str(audit._REPO / "config.py"),
            "audit_json": str(tmp_path / "audit.json"),
            "audit_md": str(tmp_path / "audit.md"),
        }
        return audit.main(
            json_out=str(tmp_path / "audit.json"),
            md_out=str(tmp_path / "audit.md"),
        )

    def test_produces_json_output_file(self, tmp_path, monkeypatch):
        # Redirect to tmp_path
        records = [_make_record(trade_id=f"T{i}") for i in range(5)]
        tr = tmp_path / "tr.jsonl"
        _write_jsonl(tr, records)
        json_out = tmp_path / "audit.json"
        md_out = tmp_path / "audit.md"
        monkeypatch.setattr(audit, "_REPO", Path(audit._REPO))  # keep real repo path
        result = audit.main(json_out=str(json_out), md_out=str(md_out))
        assert json_out.exists()

    def test_produces_markdown_output_file(self, tmp_path):
        records = [_make_record(trade_id=f"T{i}") for i in range(5)]
        tr = tmp_path / "tr.jsonl"
        _write_jsonl(tr, records)
        json_out = tmp_path / "audit.json"
        md_out = tmp_path / "audit.md"
        audit.main(json_out=str(json_out), md_out=str(md_out))
        assert md_out.exists()
        content = md_out.read_text()
        assert len(content) > 100

    def test_json_output_has_required_keys(self, tmp_path):
        records = [_make_record(trade_id=f"T{i}") for i in range(5)]
        tr = tmp_path / "tr.jsonl"
        _write_jsonl(tr, records)
        json_out = tmp_path / "audit.json"
        md_out = tmp_path / "audit.md"
        audit.main(json_out=str(json_out), md_out=str(md_out))
        data = json.loads(json_out.read_text())
        for key in ["primary_ledger", "labels", "contamination", "verdict", "ml_logic"]:
            assert key in data, f"Missing key: {key}"

    def test_json_output_has_ml_logic_verdict_key(self, tmp_path):
        records = [_make_record(trade_id=f"T{i}") for i in range(5)]
        tr = tmp_path / "tr.jsonl"
        _write_jsonl(tr, records)
        json_out = tmp_path / "audit.json"
        md_out = tmp_path / "audit.md"
        audit.main(json_out=str(json_out), md_out=str(md_out))
        data = json.loads(json_out.read_text())
        assert "ml_logic_verdict" in data["ml_logic"]

    def test_markdown_output_has_verdict_section(self, tmp_path):
        records = [_make_record(trade_id=f"T{i}") for i in range(5)]
        tr = tmp_path / "tr.jsonl"
        _write_jsonl(tr, records)
        json_out = tmp_path / "audit.json"
        md_out = tmp_path / "audit.md"
        audit.main(json_out=str(json_out), md_out=str(md_out))
        content = md_out.read_text()
        assert "## Verdict:" in content
        assert "## ML Logic Verdict:" in content
        assert "## Recommendations" in content
        assert "Anti-Bloat" in content

    def test_handles_completely_empty_ledger(self, tmp_path):
        tr = tmp_path / "tr.jsonl"
        tr.write_text("")  # empty file
        json_out = tmp_path / "audit.json"
        md_out = tmp_path / "audit.md"
        # Should not raise
        try:
            audit.main(json_out=str(json_out), md_out=str(md_out))
        except Exception as exc:
            pytest.fail(f"Raised with empty ledger: {exc}")
        assert json_out.exists()
