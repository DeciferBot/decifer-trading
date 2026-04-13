"""Tests for get_thesis_performance() — GAP-004: exit_reason → entry reasoning feedback."""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pattern_library

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_library(records: list[dict]) -> dict:
    """Build a pattern_library dict from a list of records."""
    return {r["pattern_id"]: r for r in records}


def _record(
    pattern_id: str,
    trade_type: str = "SCALP",
    pnl: float = 100.0,
    pnl_pct: float | None = None,
    exit_reason: str = "sl_hit | SCALP | regime:BULL→BULL | held:30min | thesis:noise_stop",
) -> dict:
    if pnl_pct is None:
        pnl_pct = 0.01 if pnl >= 0 else -0.01
    return {
        "pattern_id": pattern_id,
        "trade_type": trade_type,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "exit_reason": exit_reason,
    }


# ── Basic aggregation ─────────────────────────────────────────────────────────


def test_returns_empty_when_no_completed_patterns(tmp_path):
    lib = {}
    with patch.object(pattern_library, "LIBRARY_PATH", tmp_path / "pl.json"):
        (tmp_path / "pl.json").write_text(json.dumps(lib))
        result = pattern_library.get_thesis_performance(min_samples=1)
    assert result == []


def test_aggregates_by_trade_type_and_thesis_class(tmp_path):
    records = [
        _record("a1", "SCALP", pnl=100, exit_reason="... | thesis:noise_stop"),
        _record("a2", "SCALP", pnl=100, exit_reason="... | thesis:noise_stop"),
        _record("a3", "SCALP", pnl=-50, exit_reason="... | thesis:noise_stop"),
    ]
    lib = _make_library(records)
    with patch.object(pattern_library, "LIBRARY_PATH", tmp_path / "pl.json"):
        (tmp_path / "pl.json").write_text(json.dumps(lib))
        result = pattern_library.get_thesis_performance(min_samples=1)

    assert len(result) == 1
    row = result[0]
    assert row["trade_type"] == "SCALP"
    assert row["thesis_class"] == "noise_stop"
    assert row["count"] == 3
    assert row["win_rate"] == round(2 / 3, 2)


def test_min_samples_filters_small_groups(tmp_path):
    records = [
        _record("b1", "SCALP", exit_reason="... | thesis:noise_stop"),
        _record("b2", "SCALP", exit_reason="... | thesis:noise_stop"),
        _record("b3", "SWING", exit_reason="... | thesis:confirmed"),  # only 1 → filtered
    ]
    lib = _make_library(records)
    with patch.object(pattern_library, "LIBRARY_PATH", tmp_path / "pl.json"):
        (tmp_path / "pl.json").write_text(json.dumps(lib))
        result = pattern_library.get_thesis_performance(min_samples=2)

    assert len(result) == 1
    assert result[0]["trade_type"] == "SCALP"


def test_distinct_combinations_returned_separately(tmp_path):
    records = [
        _record("c1", "SCALP", pnl=100, exit_reason="... | thesis:noise_stop"),
        _record("c2", "SCALP", pnl=100, exit_reason="... | thesis:noise_stop"),
        _record("c3", "SCALP", pnl=100, exit_reason="... | thesis:noise_stop"),
        _record("c4", "SWING", pnl=200, exit_reason="... | thesis:confirmed"),
        _record("c5", "SWING", pnl=200, exit_reason="... | thesis:confirmed"),
        _record("c6", "SWING", pnl=200, exit_reason="... | thesis:confirmed"),
    ]
    lib = _make_library(records)
    with patch.object(pattern_library, "LIBRARY_PATH", tmp_path / "pl.json"):
        (tmp_path / "pl.json").write_text(json.dumps(lib))
        result = pattern_library.get_thesis_performance(min_samples=1)

    combos = {(r["trade_type"], r["thesis_class"]) for r in result}
    assert ("SCALP", "noise_stop") in combos
    assert ("SWING", "confirmed") in combos


def test_falls_back_to_raw_exit_reason_when_no_thesis_token(tmp_path):
    records = [
        _record("d1", "SCALP", exit_reason="manual"),
        _record("d2", "SCALP", exit_reason="manual"),
        _record("d3", "SCALP", exit_reason="manual"),
    ]
    lib = _make_library(records)
    with patch.object(pattern_library, "LIBRARY_PATH", tmp_path / "pl.json"):
        (tmp_path / "pl.json").write_text(json.dumps(lib))
        result = pattern_library.get_thesis_performance(min_samples=1)

    assert result[0]["thesis_class"] == "manual"


def test_skips_incomplete_patterns(tmp_path):
    # pnl=None → not completed
    records = [
        {"pattern_id": "e1", "trade_type": "SCALP", "pnl": None, "exit_reason": "... | thesis:noise_stop"},
    ]
    lib = _make_library(records)
    with patch.object(pattern_library, "LIBRARY_PATH", tmp_path / "pl.json"):
        (tmp_path / "pl.json").write_text(json.dumps(lib))
        result = pattern_library.get_thesis_performance(min_samples=1)

    assert result == []


def test_win_rate_100_percent(tmp_path):
    records = [
        _record("f1", "SWING", pnl=200, exit_reason="... | thesis:confirmed"),
        _record("f2", "SWING", pnl=300, exit_reason="... | thesis:confirmed"),
        _record("f3", "SWING", pnl=100, exit_reason="... | thesis:confirmed"),
    ]
    lib = _make_library(records)
    with patch.object(pattern_library, "LIBRARY_PATH", tmp_path / "pl.json"):
        (tmp_path / "pl.json").write_text(json.dumps(lib))
        result = pattern_library.get_thesis_performance(min_samples=1)

    assert result[0]["win_rate"] == 1.0


def test_sorted_by_count_descending(tmp_path):
    records = [_record(f"g{i}", "SCALP", exit_reason="... | thesis:noise_stop") for i in range(5)] + [
        _record(f"h{i}", "SWING", exit_reason="... | thesis:confirmed") for i in range(2)
    ]
    lib = _make_library(records)
    with patch.object(pattern_library, "LIBRARY_PATH", tmp_path / "pl.json"):
        (tmp_path / "pl.json").write_text(json.dumps(lib))
        result = pattern_library.get_thesis_performance(min_samples=1)

    assert result[0]["count"] >= result[-1]["count"]
