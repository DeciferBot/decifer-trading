# tests/test_ml_legacy_removed.py
# Sprint 1 — ML Clean-Slate proof tests.
# These tests assert that the legacy ML engine has been fully removed and that
# no production code path can reach it.  They are permanent — they must pass
# in every future session as proof that the contaminated engine never returns.

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


# ── T1: ml_engine.py does not exist ───────────────────────────────────────────

def test_T1_ml_engine_file_deleted():
    """ml_engine.py must not exist in the repository."""
    assert not (_REPO / "ml_engine.py").exists(), (
        "ml_engine.py still exists. It was deleted in Sprint 1 (2026-05-20) "
        "because it contained holding_minutes leakage and a broken inference path."
    )


# ── T2: enhance_score() and SignalEnhancer cannot be imported ─────────────────

def test_T2_enhance_score_cannot_be_imported():
    """enhance_score() and SignalEnhancer must be unimportable."""
    # Clear any stale cache entry that might have been set earlier in the test session.
    for mod_name in list(sys.modules.keys()):
        if "ml_engine" in mod_name:
            del sys.modules[mod_name]

    with pytest.raises((ImportError, ModuleNotFoundError)):
        import ml_engine  # noqa: F401

    # Also ensure the symbols are gone from any namespace
    assert "ml_engine" not in sys.modules


# ── T3: leaky models not in runtime load path ─────────────────────────────────

def test_T3_no_pkl_files_in_models_dir():
    """data/models/ must contain no .pkl files."""
    models_dir = _REPO / "data" / "models"
    pkl_files = list(models_dir.glob("*.pkl")) if models_dir.exists() else []
    assert not pkl_files, (
        f"Found .pkl files in data/models/: {[f.name for f in pkl_files]}. "
        "Leaky models (holding_minutes leakage, importance=0.275) must be quarantined, "
        "not in the runtime model path."
    )


def test_T3_quarantine_readme_exists():
    """Quarantine directory must have a README explaining why models were removed."""
    quarantine = _REPO / "data" / "quarantine" / "leaky_ml_models_2026_05_20"
    assert quarantine.exists(), "Quarantine directory missing"
    assert (quarantine / "QUARANTINE_README.md").exists(), (
        "Quarantine README missing — must explain the holding_minutes leakage."
    )


# ── T4: no production import path reaches legacy ML ──────────────────────────

def test_T4_bot_py_does_not_import_ml_engine():
    """bot.py must not import from ml_engine."""
    src = (_REPO / "bot.py").read_text(encoding="utf-8")
    assert "from ml_engine" not in src, "bot.py still imports from ml_engine"
    assert "import ml_engine" not in src, "bot.py still imports ml_engine"


def test_T4_signal_dispatcher_no_ml_engine():
    """signal_dispatcher.py must not import from ml_engine."""
    src = (_REPO / "signal_dispatcher.py").read_text(encoding="utf-8")
    assert "ml_engine" not in src


def test_T4_orders_core_no_ml_engine():
    """orders_core.py must not import from ml_engine."""
    src = (_REPO / "orders_core.py").read_text(encoding="utf-8")
    assert "ml_engine" not in src


def test_T4_no_production_file_imports_ml_engine():
    """No .py file outside tests/ and scripts/ must reference ml_engine."""
    violations = []
    excluded_dirs = {"tests", "scripts", ".claude", "venv", "Chief-Decifer-recovered", "__pycache__"}
    for py_file in _REPO.rglob("*.py"):
        # Skip non-production paths
        parts = set(py_file.relative_to(_REPO).parts)
        if parts & excluded_dirs:
            continue
        if "worktree" in str(py_file):
            continue
        try:
            src = py_file.read_text(encoding="utf-8", errors="replace")
            if "ml_engine" in src:
                violations.append(str(py_file.relative_to(_REPO)))
        except OSError:
            pass
    assert not violations, (
        f"Production files still reference ml_engine: {violations}"
    )


# ── T5: no config key can activate old ML engine ─────────────────────────────

def test_T5_legacy_ml_config_keys_absent():
    """Old ml_engine.py config keys must be gone from config.py."""
    # Remove any cached config module
    for mod_name in list(sys.modules.keys()):
        if mod_name == "config":
            del sys.modules[mod_name]

    import config as _cfg
    cfg = _cfg.CONFIG
    legacy_keys = {
        "ml_enabled", "ml_min_trades", "ml_retrain_interval",
        "ml_confidence_weight", "ml_models_dir",
        "ml_live_multiplier_enabled", "ml_can_block_entries", "ml_can_size_positions",
    }
    present = legacy_keys & cfg.keys()
    assert not present, (
        f"Legacy ML config keys still present: {present}. "
        "These keys only existed for the deleted ml_engine.py."
    )


def test_T5_new_reserved_ml_keys_default_false():
    """Reserved future ML keys must exist and default to False."""
    for mod_name in list(sys.modules.keys()):
        if mod_name == "config":
            del sys.modules[mod_name]

    import config as _cfg
    cfg = _cfg.CONFIG
    assert cfg.get("ml_observer_enabled") is False, (
        "ml_observer_enabled must be False — shadow observer not yet built"
    )
    assert cfg.get("ml_score_influence_enabled") is False, (
        "ml_score_influence_enabled must be False — requires explicit Amit approval"
    )


# ── T6: legacy score formula not in any production file ──────────────────────

def test_T6_legacy_score_formula_absent():
    """The formula 'base_score * (0.5 + win_prob)' must not exist in production code."""
    patterns = ["0.5 + win_prob", "0.5 + ml_pred", "enhanced_score = base_score *"]
    violations = []
    excluded_dirs = {"tests", "scripts", ".claude", "venv", "Chief-Decifer-recovered",
                     "__pycache__", "data", "docs"}
    for py_file in _REPO.rglob("*.py"):
        parts = set(py_file.relative_to(_REPO).parts)
        if parts & excluded_dirs:
            continue
        if "worktree" in str(py_file):
            continue
        try:
            src = py_file.read_text(encoding="utf-8", errors="replace")
            for pat in patterns:
                if pat in src:
                    violations.append(f"{py_file.relative_to(_REPO)}: '{pat}'")
        except OSError:
            pass
    assert not violations, (
        f"Legacy ML score formula still present: {violations}"
    )


# ── T7: holding_minutes not in any active ML feature-building path ─────────────

def test_T7_no_ml_feature_builder_references_holding_minutes():
    """No active ML feature-building file may use holding_minutes as an input feature.

    holding_minutes is a post-outcome measurement (actual trade duration).
    It must never appear as a model INPUT feature — only as an outcome attribute.
    The new architecture separates outcome storage from feature extraction.
    """
    # Only check files that could be part of a future ML feature path.
    # training_store.py and event_log.py store holding_minutes as an outcome field — OK.
    # The rule is: no training or feature-extraction script may feed holding_minutes
    # into a model input array.
    feature_extraction_files = [
        _REPO / "training_store.py",
    ]
    violations = []
    for path in feature_extraction_files:
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        # holding_minutes as a stored outcome is fine.
        # Violation: if a file builds a feature row and includes holding_minutes in it.
        # We check for the specific pattern of adding it to a feature dict/list.
        if 'features["holding_minutes"]' in src or '"holding_minutes": ' in src:
            # Distinguish: storing as an outcome attribute vs. adding to a feature row.
            # training_store stores it as an outcome — that's fine.
            # A violation would be: feature_cols.append("holding_minutes") or row["holding_minutes"]
            if "feature_cols" in src and "holding_minutes" in src:
                violations.append(str(path.relative_to(_REPO)))

    assert not violations, (
        f"Files may be building ML features with holding_minutes: {violations}. "
        "holding_minutes is post-outcome data and must never be a model input."
    )


# ── T8: evidence files are untouched ─────────────────────────────────────────

def test_T8_training_records_still_exists():
    """data/training_records.jsonl must not be deleted — it is an evidence asset."""
    assert (_REPO / "data" / "training_records.jsonl").exists(), (
        "data/training_records.jsonl is missing. "
        "This is an evidence asset and must never be deleted."
    )


def test_T8_closed_trade_ledger_still_exists():
    """data/ml/closed_trade_training_ledger.jsonl must not be deleted."""
    assert (_REPO / "data" / "ml" / "closed_trade_training_ledger.jsonl").exists(), (
        "data/ml/closed_trade_training_ledger.jsonl is missing. Evidence must be preserved."
    )


def test_T8_signals_log_still_exists():
    """data/signals_typed.jsonl or signals_log.jsonl evidence must not be deleted."""
    signals_log = _REPO / "data" / "signals_log.jsonl"
    signals_typed = _REPO / "data" / "signals_typed.jsonl"
    assert signals_log.exists() or signals_typed.exists(), (
        "Signals log is missing. Evidence must be preserved."
    )


# ── T9: live signal scoring works without ML ─────────────────────────────────

def test_T9_config_loads_without_ml_engine():
    """config.py must load without any ml_engine import."""
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("config", "ml_engine"):
            del sys.modules[mod_name]

    # This must not raise
    import config as _cfg  # noqa: F401
    assert _cfg.CONFIG is not None


def test_T9_training_store_loads_without_ml_engine():
    """training_store.py must load without ml_engine."""
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("training_store", "ml_engine"):
            del sys.modules[mod_name]

    import training_store as ts
    assert callable(ts.count_eligible)
    assert callable(ts.load)


# ── T10: order path has no ML references ─────────────────────────────────────

def test_T10_orders_core_no_ml_references():
    """orders_core.py must have no ml_engine, enhance_score, or SignalEnhancer references."""
    src = (_REPO / "orders_core.py").read_text(encoding="utf-8")
    for symbol in ("ml_engine", "enhance_score", "SignalEnhancer", "win_prob", "ml_confidence"):
        assert symbol not in src, (
            f"orders_core.py references '{symbol}' — ML must not touch the order path."
        )


def test_T10_orders_state_no_ml_references():
    """orders_state.py must have no ml_engine references."""
    src = (_REPO / "orders_state.py").read_text(encoding="utf-8")
    for symbol in ("ml_engine", "enhance_score", "SignalEnhancer"):
        assert symbol not in src, (
            f"orders_state.py references '{symbol}'."
        )
