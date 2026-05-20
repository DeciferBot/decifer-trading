# tests/test_trade_data_contract.py
"""
Tests for trade_data_contract.py

Covers:
- build_entry_snapshot: field mapping, forbidden fields, missing required fields
- write_entry_snapshot: happy path, duplicates, blank/invalid trade_id, empty
  signal_scores, invalid fill_price, never raises
- write_closed_record: loads entry snapshot, missing snapshot, missing pnl,
  duplicate, schema invalid, win_loss_label derivation, never raises
- derive_win_loss_label: WIN/LOSS/BREAKEVEN
- _append_jsonl: creates dirs, appends line, thread safety
- _load_existing_trade_ids: empty/nonexistent/populated/malformed
- ML config safety defaults

All tests use tmp_path + monkeypatch — no real data/ml/ files are touched.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import types
from pathlib import Path

import pytest


# ── Stub config so trade_data_contract can be imported cleanly ─────────────────

def _make_config_stub(tmp_ml: Path) -> None:
    """Install a config stub pointing ml_data_dir at tmp_ml."""
    mod = types.ModuleType("config")
    mod.CONFIG = {"ml_data_dir": str(tmp_ml)}
    sys.modules["config"] = mod


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def ml_dir(tmp_path):
    d = tmp_path / "data" / "ml"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def tdc(ml_dir, monkeypatch):
    """Import (or reload) trade_data_contract with paths redirected to tmp_path."""
    _make_config_stub(ml_dir)
    if "trade_data_contract" in sys.modules:
        del sys.modules["trade_data_contract"]
    import trade_data_contract as m
    # Override all path constants to point at tmp dirs.
    monkeypatch.setattr(m, "ENTRY_SNAPSHOT_FILE",      ml_dir / "entry_trade_snapshots.jsonl")
    monkeypatch.setattr(m, "CLOSED_LEDGER_FILE",       ml_dir / "closed_trade_training_ledger.jsonl")
    monkeypatch.setattr(m, "QUARANTINE_ENTRY_FILE",    ml_dir / "quarantine_entry_snapshots.jsonl")
    monkeypatch.setattr(m, "QUARANTINE_CLOSED_FILE",   ml_dir / "quarantine_closed_records.jsonl")
    monkeypatch.setattr(m, "QUARANTINE_MISSING_ENTRY", ml_dir / "quarantine_missing_entry_snapshot.jsonl")
    monkeypatch.setattr(m, "QUARANTINE_MISSING_OUTCOME", ml_dir / "quarantine_missing_outcome.jsonl")
    monkeypatch.setattr(m, "QUARANTINE_SCHEMA_INVALID",ml_dir / "quarantine_schema_invalid.jsonl")
    monkeypatch.setattr(m, "QUARANTINE_DUPLICATE_ID",  ml_dir / "quarantine_duplicate_trade_id.jsonl")
    return m


# ── Helpers ────────────────────────────────────────────────────────────────────

def _active_trade(
    trade_id="T001",
    symbol="AAPL",
    direction="LONG",
    instrument="stock",
    trade_type="INTRADAY",
    regime="BULL_TRENDING",
    signal_scores=None,
    conviction=0.8,
    score=72.0,
    open_time="2026-05-14T09:30:00+00:00",
    agent_outputs=None,
    **kwargs,
) -> dict:
    base = {
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "instrument": instrument,
        "trade_type": trade_type,
        "entry_regime": regime,
        "signal_scores": signal_scores if signal_scores is not None else {"trend": 8, "momentum": 6},
        "conviction": conviction,
        "score": score,
        "open_time": open_time,
        "agent_outputs": agent_outputs or {"candidate_source": "legacy_scanner"},
        "entry_context": {"sector_etf": "XLK", "catalyst_type": "earnings"},
    }
    base.update(kwargs)
    return base


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s:
                rows.append(json.loads(s))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# TestDeriveWinLossLabel
# ══════════════════════════════════════════════════════════════════════════════

class TestDeriveWinLossLabel:
    def test_positive_pnl_is_win(self, tdc):
        assert tdc.derive_win_loss_label(150.0) == "WIN"

    def test_negative_pnl_is_loss(self, tdc):
        assert tdc.derive_win_loss_label(-50.0) == "LOSS"

    def test_zero_pnl_is_breakeven(self, tdc):
        assert tdc.derive_win_loss_label(0.0) == "BREAKEVEN"

    def test_small_positive(self, tdc):
        assert tdc.derive_win_loss_label(0.01) == "WIN"

    def test_small_negative(self, tdc):
        assert tdc.derive_win_loss_label(-0.01) == "LOSS"


# ══════════════════════════════════════════════════════════════════════════════
# TestBuildEntrySnapshot
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildEntrySnapshot:
    def test_happy_path_all_required_fields_present(self, tdc):
        snap = tdc.build_entry_snapshot(
            active_trade=_active_trade(),
            fill_price=150.0,
            fill_qty=100,
            entry_price_source="twap_fill",
            fill_confirmed=True,
            order_id=999,
            trade_id="T001",
        )
        required = tdc._ENTRY_SNAPSHOT_REQUIRED
        for field in required:
            assert field in snap, f"Required field '{field}' missing from snapshot"

    def test_entry_price_source_stored(self, tdc):
        snap = tdc.build_entry_snapshot(
            active_trade=_active_trade(),
            fill_price=100.0,
            fill_qty=50,
            entry_price_source="bracket_fill_watcher",
            fill_confirmed=False,
        )
        assert snap["entry_price_source"] == "bracket_fill_watcher"
        assert snap["fill_confirmed"] is False

    def test_fill_confirmed_true_stored(self, tdc):
        snap = tdc.build_entry_snapshot(
            active_trade=_active_trade(),
            fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert snap["fill_confirmed"] is True

    def test_regime_extracted_from_entry_regime(self, tdc):
        at = _active_trade(regime="BEAR_TRENDING")
        snap = tdc.build_entry_snapshot(
            active_trade=at, fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert snap["regime"] == "BEAR_TRENDING"

    def test_session_character_from_entry_context(self, tdc):
        at = _active_trade()
        at["entry_context"] = {"sector_etf": "XLK", "session_character": "MORNING_DRIVE"}
        snap = tdc.build_entry_snapshot(
            active_trade=at, fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert snap["session_character"] == "MORNING_DRIVE"

    def test_candidate_source_from_agent_outputs(self, tdc):
        at = _active_trade(agent_outputs={"candidate_source": "position_research_universe"})
        snap = tdc.build_entry_snapshot(
            active_trade=at, fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert snap["candidate_source"] == "position_research_universe"

    def test_missing_field_flags_populated_for_empty_signal_scores(self, tdc):
        at = _active_trade(signal_scores={})
        snap = tdc.build_entry_snapshot(
            active_trade=at, fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert "signal_scores" in snap["missing_field_flags"]

    def test_missing_field_flags_populated_for_unknown_regime(self, tdc):
        at = _active_trade(regime="UNKNOWN")
        snap = tdc.build_entry_snapshot(
            active_trade=at, fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert "regime" in snap["missing_field_flags"]

    def test_exit_fields_do_not_appear_in_snapshot(self, tdc):
        snap = tdc.build_entry_snapshot(
            active_trade=_active_trade(), fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        for forbidden in tdc._ENTRY_FORBIDDEN_FIELDS:
            assert forbidden not in snap, f"Forbidden field '{forbidden}' found in snapshot"

    def test_raises_if_forbidden_field_in_active_trade(self, tdc):
        at = _active_trade()
        at["exit_price"] = 200.0  # This lands in snap if builder doesn't guard it
        # build_entry_snapshot should not blow up just because the active_trade has
        # extra keys — it only checks what it outputs. Confirm snap excludes them.
        snap = tdc.build_entry_snapshot(
            active_trade=at, fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert "exit_price" not in snap

    def test_schema_version_is_1_0(self, tdc):
        snap = tdc.build_entry_snapshot(
            active_trade=_active_trade(), fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert snap["schema_version"] == "1.0"


# ══════════════════════════════════════════════════════════════════════════════
# TestWriteEntrySnapshot
# ══════════════════════════════════════════════════════════════════════════════

class TestWriteEntrySnapshot:
    def test_happy_path_appends_one_line(self, tdc):
        result = tdc.write_entry_snapshot(
            trade_id="T001",
            active_trade_copy=_active_trade(trade_id="T001"),
            fill_price=150.0,
            fill_qty=100,
            entry_price_source="twap_fill",
            fill_confirmed=True,
        )
        assert result is True
        rows = _read_jsonl(tdc.ENTRY_SNAPSHOT_FILE)
        assert len(rows) == 1
        assert rows[0]["trade_id"] == "T001"

    def test_duplicate_trade_id_goes_to_quarantine_not_main(self, tdc):
        tdc.write_entry_snapshot(
            trade_id="T002",
            active_trade_copy=_active_trade(trade_id="T002"),
            fill_price=150.0, fill_qty=100,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        result = tdc.write_entry_snapshot(
            trade_id="T002",
            active_trade_copy=_active_trade(trade_id="T002"),
            fill_price=155.0, fill_qty=100,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert result is False
        main_rows = _read_jsonl(tdc.ENTRY_SNAPSHOT_FILE)
        assert len(main_rows) == 1  # only the first write

        dup_rows = _read_jsonl(tdc.QUARANTINE_DUPLICATE_ID)
        assert len(dup_rows) == 1
        assert dup_rows[0]["quarantine_reason"] == "duplicate_entry_snapshot"

    def test_blank_trade_id_quarantine_only(self, tdc):
        result = tdc.write_entry_snapshot(
            trade_id="",
            active_trade_copy=_active_trade(trade_id=""),
            fill_price=150.0, fill_qty=100,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert result is False
        assert not tdc.ENTRY_SNAPSHOT_FILE.exists() or len(_read_jsonl(tdc.ENTRY_SNAPSHOT_FILE)) == 0
        q_rows = _read_jsonl(tdc.QUARANTINE_ENTRY_FILE)
        assert any(r.get("quarantine_reason") == "blank_trade_id" for r in q_rows)

    def test_invalid_direction_quarantine_only(self, tdc):
        result = tdc.write_entry_snapshot(
            trade_id="T003",
            active_trade_copy=_active_trade(trade_id="T003", direction="BUY"),
            fill_price=150.0, fill_qty=100,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert result is False
        assert not tdc.ENTRY_SNAPSHOT_FILE.exists() or len(_read_jsonl(tdc.ENTRY_SNAPSHOT_FILE)) == 0
        q_rows = _read_jsonl(tdc.QUARANTINE_ENTRY_FILE)
        assert any("invalid_direction" in (r.get("quarantine_reason") or "") for r in q_rows)

    def test_empty_signal_scores_writes_main_and_quarantine(self, tdc):
        result = tdc.write_entry_snapshot(
            trade_id="T004",
            active_trade_copy=_active_trade(trade_id="T004", signal_scores={}),
            fill_price=150.0, fill_qty=100,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert result is True
        main_rows = _read_jsonl(tdc.ENTRY_SNAPSHOT_FILE)
        assert len(main_rows) == 1

        q_rows = _read_jsonl(tdc.QUARANTINE_ENTRY_FILE)
        assert any(r.get("quarantine_reason") == "empty_signal_scores" for r in q_rows)

    def test_invalid_fill_price_writes_main_and_quarantine(self, tdc):
        result = tdc.write_entry_snapshot(
            trade_id="T005",
            active_trade_copy=_active_trade(trade_id="T005"),
            fill_price=0.0, fill_qty=100,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert result is True
        main_rows = _read_jsonl(tdc.ENTRY_SNAPSHOT_FILE)
        assert len(main_rows) == 1

        q_rows = _read_jsonl(tdc.QUARANTINE_ENTRY_FILE)
        assert any(r.get("quarantine_reason") == "invalid_fill_price" for r in q_rows)

    def test_returns_bool_not_exception(self, tdc, monkeypatch):
        """Even if _append_jsonl raises, write_entry_snapshot returns False."""
        def _raise(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(tdc, "_append_jsonl", _raise)
        result = tdc.write_entry_snapshot(
            trade_id="T006",
            active_trade_copy=_active_trade(trade_id="T006"),
            fill_price=100.0, fill_qty=10,
            entry_price_source="twap_fill", fill_confirmed=True,
        )
        assert isinstance(result, bool)

    def test_multiple_unique_trades_all_written(self, tdc):
        for i in range(5):
            tdc.write_entry_snapshot(
                trade_id=f"TM{i:03d}",
                active_trade_copy=_active_trade(trade_id=f"TM{i:03d}"),
                fill_price=100.0 + i, fill_qty=10,
                entry_price_source="twap_fill", fill_confirmed=True,
            )
        rows = _read_jsonl(tdc.ENTRY_SNAPSHOT_FILE)
        assert len(rows) == 5


# ══════════════════════════════════════════════════════════════════════════════
# TestWriteClosedRecord
# ══════════════════════════════════════════════════════════════════════════════

def _write_snapshot(tdc, trade_id: str, **kw) -> None:
    """Helper: write one entry snapshot to the tmp ledger."""
    tdc.write_entry_snapshot(
        trade_id=trade_id,
        active_trade_copy=_active_trade(trade_id=trade_id, **kw),
        fill_price=kw.get("fill_price", 150.0),
        fill_qty=kw.get("fill_qty", 100),
        entry_price_source="twap_fill",
        fill_confirmed=True,
    )


class TestWriteClosedRecord:
    def test_happy_path_loads_snapshot_and_writes(self, tdc):
        _write_snapshot(tdc, "C001")
        result = tdc.write_closed_record(
            trade_id="C001",
            exit_price=160.0,
            realised_pnl=1000.0,
            exit_reason="tp_hit",
            hold_minutes=45,
            outcome_source="execute_sell",
        )
        assert result is True
        rows = _read_jsonl(tdc.CLOSED_LEDGER_FILE)
        assert len(rows) == 1
        row = rows[0]
        assert row["trade_id"] == "C001"
        assert row["exit_price"] == 160.0
        assert row["realised_pnl"] == 1000.0
        assert row["win_loss_label"] == "WIN"
        assert row["hold_minutes"] == 45
        assert row["exit_reason"] == "tp_hit"

    def test_win_loss_label_loss(self, tdc):
        _write_snapshot(tdc, "C002")
        tdc.write_closed_record(
            trade_id="C002", exit_price=140.0, realised_pnl=-500.0,
            exit_reason="sl_hit", hold_minutes=20, outcome_source="execute_sell",
        )
        rows = _read_jsonl(tdc.CLOSED_LEDGER_FILE)
        assert rows[0]["win_loss_label"] == "LOSS"

    def test_win_loss_label_breakeven(self, tdc):
        _write_snapshot(tdc, "C003")
        tdc.write_closed_record(
            trade_id="C003", exit_price=150.0, realised_pnl=0.0,
            exit_reason="manual", hold_minutes=10, outcome_source="execute_sell",
        )
        rows = _read_jsonl(tdc.CLOSED_LEDGER_FILE)
        assert rows[0]["win_loss_label"] == "BREAKEVEN"

    def test_missing_entry_snapshot_quarantined(self, tdc):
        result = tdc.write_closed_record(
            trade_id="MISSING_SNAP",
            exit_price=160.0,
            realised_pnl=1000.0,
            exit_reason="tp_hit",
        )
        assert result is False
        assert not tdc.CLOSED_LEDGER_FILE.exists() or len(_read_jsonl(tdc.CLOSED_LEDGER_FILE)) == 0
        q_rows = _read_jsonl(tdc.QUARANTINE_MISSING_ENTRY)
        assert any(r.get("trade_id") == "MISSING_SNAP" for r in q_rows)

    def test_none_realised_pnl_quarantined(self, tdc):
        _write_snapshot(tdc, "C004")
        result = tdc.write_closed_record(
            trade_id="C004", exit_price=160.0, realised_pnl=None,
            exit_reason="tp_hit",
        )
        assert result is False
        q_rows = _read_jsonl(tdc.QUARANTINE_MISSING_OUTCOME)
        assert any(r.get("quarantine_reason") == "realised_pnl_is_none" for r in q_rows)

    def test_duplicate_closed_record_quarantined(self, tdc):
        _write_snapshot(tdc, "C005")
        tdc.write_closed_record(
            trade_id="C005", exit_price=160.0, realised_pnl=500.0,
            exit_reason="tp_hit", hold_minutes=30,
        )
        result = tdc.write_closed_record(
            trade_id="C005", exit_price=162.0, realised_pnl=600.0,
            exit_reason="tp_hit", hold_minutes=31,
        )
        assert result is False
        main_rows = _read_jsonl(tdc.CLOSED_LEDGER_FILE)
        assert len(main_rows) == 1  # only first write

        dup_rows = _read_jsonl(tdc.QUARANTINE_DUPLICATE_ID)
        assert any(r.get("quarantine_reason") == "duplicate_closed_record" for r in dup_rows)

    def test_closed_record_inherits_entry_snapshot_fields(self, tdc):
        _write_snapshot(tdc, "C006", symbol="NVDA", direction="SHORT")
        tdc.write_closed_record(
            trade_id="C006", exit_price=500.0, realised_pnl=2000.0,
            exit_reason="eod_flat", hold_minutes=60,
        )
        rows = _read_jsonl(tdc.CLOSED_LEDGER_FILE)
        assert rows[0]["symbol"] == "NVDA"
        assert rows[0]["direction"] == "SHORT"

    def test_never_raises(self, tdc, monkeypatch):
        def _raise(*args, **kwargs):
            raise RuntimeError("unexpected error")
        monkeypatch.setattr(tdc, "_load_entry_snapshot", _raise)
        result = tdc.write_closed_record(
            trade_id="C007", exit_price=100.0, realised_pnl=50.0, exit_reason="tp_hit",
        )
        assert isinstance(result, bool)

    def test_all_required_fields_in_closed_record(self, tdc):
        _write_snapshot(tdc, "C008")
        tdc.write_closed_record(
            trade_id="C008", exit_price=160.0, realised_pnl=800.0,
            exit_reason="tp_hit", hold_minutes=45,
        )
        rows = _read_jsonl(tdc.CLOSED_LEDGER_FILE)
        for field in tdc._CLOSED_RECORD_REQUIRED:
            assert field in rows[0], f"Required closed field '{field}' missing"


# ══════════════════════════════════════════════════════════════════════════════
# TestAppendJsonl
# ══════════════════════════════════════════════════════════════════════════════

class TestAppendJsonl:
    def test_creates_parent_dirs(self, tdc, tmp_path):
        target = tmp_path / "deep" / "nested" / "file.jsonl"
        tdc._append_jsonl(target, {"x": 1})
        assert target.exists()

    def test_each_call_appends_exactly_one_line(self, tdc, tmp_path):
        target = tmp_path / "out.jsonl"
        tdc._append_jsonl(target, {"n": 1})
        tdc._append_jsonl(target, {"n": 2})
        tdc._append_jsonl(target, {"n": 3})
        lines = [l for l in target.read_text().splitlines() if l.strip()]
        assert len(lines) == 3

    def test_thread_safety_concurrent_writes(self, tdc, tmp_path):
        target = tmp_path / "concurrent.jsonl"
        errors = []

        def _write(i):
            try:
                tdc._append_jsonl(target, {"i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        lines = [l for l in target.read_text().splitlines() if l.strip()]
        assert len(lines) == 20
        # Each line must be valid JSON.
        for line in lines:
            json.loads(line)


# ══════════════════════════════════════════════════════════════════════════════
# TestLoadExistingTradeIds
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadExistingTradeIds:
    def test_empty_file_returns_empty_frozenset(self, tdc, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        result = tdc._load_existing_trade_ids(f)
        assert result == frozenset()

    def test_nonexistent_file_returns_empty_frozenset(self, tdc, tmp_path):
        result = tdc._load_existing_trade_ids(tmp_path / "nonexistent.jsonl")
        assert result == frozenset()

    def test_populated_file_returns_correct_ids(self, tdc, tmp_path):
        f = tmp_path / "ids.jsonl"
        f.write_text(
            json.dumps({"trade_id": "A"}) + "\n"
            + json.dumps({"trade_id": "B"}) + "\n"
            + json.dumps({"trade_id": "C"}) + "\n"
        )
        result = tdc._load_existing_trade_ids(f)
        assert result == frozenset({"A", "B", "C"})

    def test_malformed_json_lines_skipped_gracefully(self, tdc, tmp_path):
        f = tmp_path / "mixed.jsonl"
        f.write_text(
            json.dumps({"trade_id": "X"}) + "\n"
            "not json at all\n"
            + json.dumps({"trade_id": "Y"}) + "\n"
        )
        result = tdc._load_existing_trade_ids(f)
        assert "X" in result
        assert "Y" in result

    def test_records_without_trade_id_skipped(self, tdc, tmp_path):
        f = tmp_path / "noid.jsonl"
        f.write_text(
            json.dumps({"symbol": "AAPL"}) + "\n"
            + json.dumps({"trade_id": "Z"}) + "\n"
        )
        result = tdc._load_existing_trade_ids(f)
        assert result == frozenset({"Z"})

    def test_returns_frozenset_type(self, tdc, tmp_path):
        f = tmp_path / "t.jsonl"
        f.write_text(json.dumps({"trade_id": "Q"}) + "\n")
        result = tdc._load_existing_trade_ids(f)
        assert isinstance(result, frozenset)


# ══════════════════════════════════════════════════════════════════════════════
# TestMLConfigDefaults
# ══════════════════════════════════════════════════════════════════════════════

class TestMLConfigDefaults:
    def _get_real_config(self) -> dict:
        """Load config module with the real CONFIG dict (not the test stub)."""
        if "config" in sys.modules:
            del sys.modules["config"]
        import config
        return config.CONFIG

    def test_ml_observer_enabled_true_sprint35(self):
        """Sprint 3.5: ml_observer_enabled must be True — evidence collection activated."""
        cfg = self._get_real_config()
        assert cfg.get("ml_observer_enabled") is True, (
            "ml_observer_enabled must be True — Sprint 3.5 activated evidence collection"
        )

    def test_ml_score_influence_enabled_defaults_false(self):
        cfg = self._get_real_config()
        assert cfg.get("ml_score_influence_enabled") is False, (
            "ml_score_influence_enabled must default to False — requires explicit Amit approval"
        )

    def test_legacy_ml_keys_absent_from_config(self):
        """Old ml_engine.py keys must be gone — they only existed for the deleted engine."""
        cfg = self._get_real_config()
        legacy_keys = {"ml_enabled", "ml_min_trades", "ml_retrain_interval",
                       "ml_confidence_weight", "ml_models_dir",
                       "ml_live_multiplier_enabled", "ml_can_block_entries",
                       "ml_can_size_positions"}
        present = legacy_keys & cfg.keys()
        assert not present, f"Legacy ML config keys still present: {present}"


# ══════════════════════════════════════════════════════════════════════════════
# TestNoProtectedImports
# ══════════════════════════════════════════════════════════════════════════════

class TestNoProtectedImports:
    _BANNED_MODULES = {
        "orders_core", "orders_state", "orders_portfolio", "orders_options",
        "bot_state", "ib", "ibkr", "risk", "position_sizer",
    }

    def test_trade_data_contract_has_no_broker_imports(self, tdc):
        """trade_data_contract must not import execution, broker, or risk modules."""
        contract_path = Path(__file__).parent.parent / "trade_data_contract.py"
        source = contract_path.read_text(encoding="utf-8")

        # Simple import-statement check (doesn't exec the module).
        import ast as _ast
        tree = _ast.parse(source)
        imported = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, _ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])

        violations = imported & self._BANNED_MODULES
        assert not violations, (
            f"trade_data_contract imports banned module(s): {violations}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TestRebuildScript
# ══════════════════════════════════════════════════════════════════════════════

class TestRebuildScript:
    @pytest.fixture()
    def rebuild_mod(self, tmp_path, monkeypatch):
        """Import rebuild script with all paths redirected to tmp_path."""
        _make_config_stub(tmp_path / "data" / "ml")
        if "rebuild_closed_trade_training_ledger" in sys.modules:
            del sys.modules["rebuild_closed_trade_training_ledger"]
        scripts_path = Path(__file__).parent.parent / "scripts"
        sys.path.insert(0, str(scripts_path))
        import rebuild_closed_trade_training_ledger as rb
        ml_dir = tmp_path / "data" / "ml"
        ml_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rb, "_LEGACY_FILE",    tmp_path / "training_records.jsonl")
        monkeypatch.setattr(rb, "_EVENTS_FILE",    tmp_path / "trade_events.jsonl")
        monkeypatch.setattr(rb, "_SNAPSHOTS_FILE", ml_dir / "entry_trade_snapshots.jsonl")
        monkeypatch.setattr(rb, "_REBUILT_FILE",   ml_dir / "closed_trade_training_ledger.rebuilt.jsonl")
        monkeypatch.setattr(rb, "_QUARANTINE",     ml_dir / "rebuild_quarantine.jsonl")
        monkeypatch.setattr(rb, "_REPORT_MD",      tmp_path / "docs" / "rebuild_report.md")
        return rb

    def _write_legacy(self, path: Path, records: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_does_not_mutate_source_file(self, rebuild_mod, tmp_path):
        legacy = tmp_path / "training_records.jsonl"
        rec = {"trade_id": "R001", "symbol": "AAPL", "pnl": 100.0}
        self._write_legacy(legacy, [rec])
        original_content = legacy.read_text()
        rebuild_mod.rebuild(dry_run=False)
        assert legacy.read_text() == original_content

    def test_writes_to_rebuilt_file_not_canonical(self, rebuild_mod, tmp_path):
        legacy = tmp_path / "training_records.jsonl"
        self._write_legacy(legacy, [{"trade_id": "R002", "symbol": "TSLA"}])
        rebuild_mod.rebuild(dry_run=False)
        # Canonical file must NOT be created.
        canonical = tmp_path / "data" / "ml" / "closed_trade_training_ledger.jsonl"
        assert not canonical.exists(), "Rebuild must never write to canonical ledger"

    def test_rebuilt_records_marked_legacy(self, rebuild_mod, tmp_path):
        legacy = tmp_path / "training_records.jsonl"
        self._write_legacy(legacy, [{"trade_id": "R003", "symbol": "MSFT"}])
        rebuild_mod.rebuild(dry_run=False)
        rebuilt = tmp_path / "data" / "ml" / "closed_trade_training_ledger.rebuilt.jsonl"
        if rebuilt.exists():
            rows = _read_jsonl(rebuilt)
            for row in rows:
                assert row.get("rebuilt_from_legacy") is True

    def test_duplicate_trade_id_quarantined(self, rebuild_mod, tmp_path):
        legacy = tmp_path / "training_records.jsonl"
        self._write_legacy(legacy, [
            {"trade_id": "R004", "symbol": "AAPL"},
            {"trade_id": "R004", "symbol": "AAPL"},  # duplicate
        ])
        stats = rebuild_mod.rebuild(dry_run=False)
        assert stats["skipped_duplicate"] >= 1

    def test_dry_run_writes_nothing(self, rebuild_mod, tmp_path):
        legacy = tmp_path / "training_records.jsonl"
        self._write_legacy(legacy, [{"trade_id": "R005", "symbol": "GOOG"}])
        rebuild_mod.rebuild(dry_run=True)
        rebuilt = tmp_path / "data" / "ml" / "closed_trade_training_ledger.rebuilt.jsonl"
        assert not rebuilt.exists() or len(_read_jsonl(rebuilt)) == 0

    def test_missing_trade_id_quarantined_not_rebuilt(self, rebuild_mod, tmp_path):
        legacy = tmp_path / "training_records.jsonl"
        self._write_legacy(legacy, [{"trade_id": "", "symbol": "AMD"}])
        stats = rebuild_mod.rebuild(dry_run=False)
        assert stats["quarantined_blank_id"] >= 1
        rebuilt = tmp_path / "data" / "ml" / "closed_trade_training_ledger.rebuilt.jsonl"
        assert not rebuilt.exists() or len(_read_jsonl(rebuilt)) == 0

    def test_unknown_regime_preserved_in_rebuilt(self, rebuild_mod, tmp_path):
        legacy = tmp_path / "training_records.jsonl"
        self._write_legacy(legacy, [{"trade_id": "R006", "symbol": "META", "regime": "UNKNOWN"}])
        rebuild_mod.rebuild(dry_run=False)
        rebuilt = tmp_path / "data" / "ml" / "closed_trade_training_ledger.rebuilt.jsonl"
        if rebuilt.exists():
            rows = _read_jsonl(rebuilt)
            assert any(r.get("regime") == "UNKNOWN" for r in rows)
