"""
tests/test_rotation_observability.py — Unit tests for rotation_observability.py.

All I/O goes through temp directories — no production files touched.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import threading
from datetime import date, datetime, timezone

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

import rotation_observability as ro
import rotation_shadow_report as rsr

UTC = timezone.utc


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_block(obs_dir: pathlib.Path, **kwargs) -> None:
    """Redirect write_margin_block output to a temp obs_dir."""
    original = ro._OBS_DIR
    try:
        ro._OBS_DIR = str(obs_dir)
        ro._BLOCKS_PATH = str(obs_dir / "margin_blocks.jsonl")
        ro._SNAPSHOTS_PATH = str(obs_dir / "position_snapshots.jsonl")
        ro.write_margin_block(**kwargs)
    finally:
        ro._OBS_DIR = original
        ro._BLOCKS_PATH = str(pathlib.Path(original) / "margin_blocks.jsonl")
        ro._SNAPSHOTS_PATH = str(pathlib.Path(original) / "position_snapshots.jsonl")


def _write_snapshot(obs_dir: pathlib.Path, **kwargs) -> None:
    original = ro._OBS_DIR
    try:
        ro._OBS_DIR = str(obs_dir)
        ro._SNAPSHOTS_PATH = str(obs_dir / "position_snapshots.jsonl")
        ro.write_position_snapshot(**kwargs)
    finally:
        ro._OBS_DIR = original
        ro._SNAPSHOTS_PATH = str(pathlib.Path(original) / "position_snapshots.jsonl")


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ─────────────────────────────────────────────────────────────────────────────
# TestWriteMarginBlock
# ─────────────────────────────────────────────────────────────────────────────

class TestWriteMarginBlock:

    def test_writes_jsonl_record(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        _write_block(
            obs_dir,
            symbol="AVGO",
            candidate_score=80,
            direction="LONG",
            exp_code="max_positions",
            exp_reason="too many open positions",
            estimated_notional=95700.0,
            portfolio_value=957000.0,
            open_position_count=10,
            max_positions=10,
            max_alloc_pct=1.0,
            max_single_pct=0.10,
            active_trades=None,
        )
        records = _read_jsonl(obs_dir / "margin_blocks.jsonl")
        assert len(records) == 1
        r = records[0]
        assert r["symbol"] == "AVGO"
        assert r["candidate_score"] == 80
        assert r["direction"] == "LONG"
        assert r["exp_code"] == "max_positions"
        assert r["estimated_notional"] == 95700.0
        assert r["notional_is_estimate"] is True
        assert r["portfolio_value"] == 957000.0
        assert r["open_position_count"] == 10

    def test_ts_is_iso_utc(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        _write_block(
            obs_dir,
            symbol="TSLA",
            candidate_score=55,
            direction="LONG",
            exp_code="exposure_block",
            exp_reason="portfolio cap",
            estimated_notional=90000.0,
            portfolio_value=900000.0,
            open_position_count=8,
            max_positions=10,
            max_alloc_pct=1.0,
            max_single_pct=0.10,
            active_trades=None,
        )
        records = _read_jsonl(obs_dir / "margin_blocks.jsonl")
        ts = datetime.fromisoformat(records[0]["ts"])
        assert ts.tzinfo is not None

    def test_also_writes_snapshot_when_active_trades_provided(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        active = {
            "AAPL": {"symbol": "AAPL", "score": 60, "qty": 50, "entry": 180.0},
            "NVDA": {"symbol": "NVDA", "score": 30, "qty": 20, "entry": 900.0},
        }
        _write_block(
            obs_dir,
            symbol="AVGO",
            candidate_score=80,
            direction="LONG",
            exp_code="max_positions",
            exp_reason="cap",
            estimated_notional=95000.0,
            portfolio_value=950000.0,
            open_position_count=2,
            max_positions=10,
            max_alloc_pct=1.0,
            max_single_pct=0.10,
            active_trades=active,
        )
        snap_records = _read_jsonl(obs_dir / "position_snapshots.jsonl")
        assert len(snap_records) == 1
        assert "margin_block:AVGO" in snap_records[0]["trigger"]
        assert "AAPL" in snap_records[0]["positions"]

    def test_skips_reserved_positions_in_snapshot(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        active = {
            "AAPL": {"symbol": "AAPL", "score": 60, "qty": 50, "entry": 180.0},
            "MSFT": {"symbol": "MSFT", "status": "RESERVED"},
        }
        _write_block(
            obs_dir,
            symbol="AVGO",
            candidate_score=80,
            direction="LONG",
            exp_code="max_positions",
            exp_reason="cap",
            estimated_notional=95000.0,
            portfolio_value=950000.0,
            open_position_count=2,
            max_positions=10,
            max_alloc_pct=1.0,
            max_single_pct=0.10,
            active_trades=active,
        )
        snap_records = _read_jsonl(obs_dir / "position_snapshots.jsonl")
        positions = snap_records[0]["positions"]
        assert "AAPL" in positions
        assert "MSFT" not in positions

    def test_never_raises_on_write_failure(self, tmp_path):
        # Point to a non-writable path — must not raise
        try:
            ro.write_margin_block(
                symbol="TEST",
                candidate_score=50,
                direction="LONG",
                exp_code="test",
                exp_reason="test",
                estimated_notional=1.0,
                portfolio_value=100.0,
                open_position_count=1,
                max_positions=10,
                max_alloc_pct=1.0,
                max_single_pct=0.10,
                active_trades=None,
            )
        except Exception as exc:
            pytest.fail(f"write_margin_block raised unexpectedly: {exc}")

    def test_multiple_blocks_accumulate(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        for sym in ["AVGO", "NVDA", "TSLA"]:
            _write_block(
                obs_dir,
                symbol=sym,
                candidate_score=70,
                direction="LONG",
                exp_code="max_positions",
                exp_reason="cap",
                estimated_notional=90000.0,
                portfolio_value=900000.0,
                open_position_count=10,
                max_positions=10,
                max_alloc_pct=1.0,
                max_single_pct=0.10,
                active_trades=None,
            )
        records = _read_jsonl(obs_dir / "margin_blocks.jsonl")
        assert len(records) == 3
        assert {r["symbol"] for r in records} == {"AVGO", "NVDA", "TSLA"}


# ─────────────────────────────────────────────────────────────────────────────
# TestWritePositionSnapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestWritePositionSnapshot:

    def test_writes_snapshot_record(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        _write_snapshot(
            obs_dir,
            trigger="manual_test",
            active_trades={
                "AAPL": {"symbol": "AAPL", "score": 60, "qty": 50, "entry": 180.0},
            },
        )
        records = _read_jsonl(obs_dir / "position_snapshots.jsonl")
        assert len(records) == 1
        r = records[0]
        assert r["trigger"] == "manual_test"
        assert "AAPL" in r["positions"]
        assert r["positions"]["AAPL"]["score"] == 60

    def test_slim_position_drops_unknown_fields(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        _write_snapshot(
            obs_dir,
            trigger="t",
            active_trades={
                "AAPL": {
                    "symbol": "AAPL", "score": 60, "qty": 50, "entry": 180.0,
                    "some_runtime_field": "should_not_appear",
                    "ibkr_order_id": 12345,
                },
            },
        )
        records = _read_jsonl(obs_dir / "position_snapshots.jsonl")
        pos = records[0]["positions"]["AAPL"]
        assert "ibkr_order_id" not in pos
        assert "some_runtime_field" not in pos
        assert pos["score"] == 60

    def test_never_raises_on_missing_dir(self):
        try:
            ro.write_position_snapshot(
                trigger="test",
                active_trades={"X": {"symbol": "X", "score": 50}},
            )
        except Exception as exc:
            pytest.fail(f"write_position_snapshot raised unexpectedly: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadMarginBlocksJsonl
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadMarginBlocksJsonl:

    def _make_obs_dir(self, tmp_path: pathlib.Path) -> pathlib.Path:
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        return obs_dir

    def test_returns_empty_when_file_missing(self, tmp_path):
        obs_dir = self._make_obs_dir(tmp_path)
        from rotation_shadow_report import DataQuality, load_margin_blocks_jsonl
        dq = DataQuality()
        result = load_margin_blocks_jsonl(obs_dir, date(2026, 5, 12), dq)
        assert result == []

    def test_loads_valid_records(self, tmp_path):
        obs_dir = self._make_obs_dir(tmp_path)
        record = {
            "ts": "2026-05-12T14:00:01+00:00",
            "symbol": "AVGO",
            "candidate_score": 80,
            "direction": "LONG",
            "exp_code": "max_positions",
            "exp_reason": "too many open",
            "estimated_notional": 95700.0,
            "notional_is_estimate": True,
            "portfolio_value": 957000.0,
            "open_position_count": 10,
        }
        (obs_dir / "margin_blocks.jsonl").write_text(json.dumps(record) + "\n")
        from rotation_shadow_report import DataQuality, load_margin_blocks_jsonl
        dq = DataQuality()
        result = load_margin_blocks_jsonl(obs_dir, date(2026, 5, 12), dq)
        assert len(result) == 1
        r = result[0]
        assert r["symbol"] == "AVGO"
        assert r["candidate_score"] == 80
        assert r["block_reason"] == "max_positions"

    def test_filters_records_before_since(self, tmp_path):
        obs_dir = self._make_obs_dir(tmp_path)
        records = [
            {"ts": "2026-05-11T14:00:01+00:00", "symbol": "OLD"},
            {"ts": "2026-05-12T14:00:01+00:00", "symbol": "AVGO", "candidate_score": 80,
             "direction": "LONG", "exp_code": "x", "exp_reason": "y",
             "estimated_notional": 1.0, "portfolio_value": 1.0, "open_position_count": 1},
        ]
        (obs_dir / "margin_blocks.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )
        from rotation_shadow_report import DataQuality, load_margin_blocks_jsonl
        dq = DataQuality()
        result = load_margin_blocks_jsonl(obs_dir, date(2026, 5, 12), dq)
        assert len(result) == 1
        assert result[0]["symbol"] == "AVGO"

    def test_skips_malformed_lines(self, tmp_path):
        obs_dir = self._make_obs_dir(tmp_path)
        content = (
            "not-json\n"
            + json.dumps({"ts": "2026-05-12T14:00:01+00:00", "symbol": "AVGO",
                          "candidate_score": 80, "direction": "LONG",
                          "exp_code": "x", "exp_reason": "y",
                          "estimated_notional": 1.0, "portfolio_value": 1.0,
                          "open_position_count": 1}) + "\n"
        )
        (obs_dir / "margin_blocks.jsonl").write_text(content)
        from rotation_shadow_report import DataQuality, load_margin_blocks_jsonl
        dq = DataQuality()
        result = load_margin_blocks_jsonl(obs_dir, date(2026, 5, 12), dq)
        assert len(result) == 1
        assert result[0]["symbol"] == "AVGO"
        assert dq.warnings  # malformed line should trigger a warning


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadPositionSnapshotAt
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadPositionSnapshotAt:

    def test_returns_none_when_file_missing(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        from rotation_shadow_report import load_position_snapshot_at
        block_ts = datetime(2026, 5, 12, 14, 0, 1, tzinfo=UTC)
        result = load_position_snapshot_at(obs_dir, block_ts)
        assert result is None

    def test_returns_closest_snapshot_before_block(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        snapshots = [
            {
                "ts": "2026-05-12T13:00:00+00:00",
                "trigger": "margin_block:X",
                "positions": {"AAPL": {"symbol": "AAPL", "score": 60}},
            },
            {
                "ts": "2026-05-12T13:30:00+00:00",
                "trigger": "margin_block:Y",
                "positions": {"NVDA": {"symbol": "NVDA", "score": 40}},
            },
        ]
        (obs_dir / "position_snapshots.jsonl").write_text(
            "\n".join(json.dumps(s) for s in snapshots) + "\n"
        )
        from rotation_shadow_report import load_position_snapshot_at
        block_ts = datetime(2026, 5, 12, 14, 0, 0, tzinfo=UTC)
        result = load_position_snapshot_at(obs_dir, block_ts)
        assert result is not None
        syms = {p["symbol"] for p in result}
        assert "NVDA" in syms  # closest before block_ts

    def test_excludes_snapshots_after_block(self, tmp_path):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()
        snapshots = [
            {
                "ts": "2026-05-12T15:00:00+00:00",
                "trigger": "future",
                "positions": {"FUTURE": {"symbol": "FUTURE"}},
            },
        ]
        (obs_dir / "position_snapshots.jsonl").write_text(
            "\n".join(json.dumps(s) for s in snapshots) + "\n"
        )
        from rotation_shadow_report import load_position_snapshot_at
        block_ts = datetime(2026, 5, 12, 14, 0, 0, tzinfo=UTC)
        result = load_position_snapshot_at(obs_dir, block_ts)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# TestBuildHoldProtectedSet
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildHoldProtectedSet:

    def test_returns_empty_when_file_missing(self, tmp_path):
        from rotation_shadow_report import build_hold_protected_set
        result = build_hold_protected_set(tmp_path / "nonexistent.jsonl", date(2026, 5, 12))
        assert result == frozenset()

    def test_identifies_hold_symbols(self, tmp_path):
        audit = tmp_path / "apex_decision_audit.jsonl"
        records = [
            {"ts": "2026-05-12T14:00:00+00:00", "action": "HOLD", "symbol": "AAPL"},
            {"ts": "2026-05-12T14:00:00+00:00", "action": "EXIT", "symbol": "NVDA"},
            {"ts": "2026-05-12T14:00:00+00:00", "action": "HOLD", "symbol": "MSFT"},
        ]
        audit.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        from rotation_shadow_report import build_hold_protected_set
        result = build_hold_protected_set(audit, date(2026, 5, 12))
        assert "AAPL" in result
        assert "MSFT" in result
        assert "NVDA" not in result

    def test_excludes_old_sessions(self, tmp_path):
        audit = tmp_path / "apex_decision_audit.jsonl"
        records = [
            {"ts": "2026-05-11T14:00:00+00:00", "action": "HOLD", "symbol": "OLD"},
            {"ts": "2026-05-12T14:00:00+00:00", "action": "HOLD", "symbol": "CURRENT"},
        ]
        audit.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        from rotation_shadow_report import build_hold_protected_set
        result = build_hold_protected_set(audit, date(2026, 5, 12))
        assert "CURRENT" in result
        assert "OLD" not in result

    def test_nested_pm_actions_list(self, tmp_path):
        audit = tmp_path / "apex_decision_audit.jsonl"
        records = [
            {
                "ts": "2026-05-12T14:00:00+00:00",
                "pm_actions": [
                    {"action": "HOLD", "symbol": "GS"},
                    {"action": "TRIM", "symbol": "JPM"},
                ],
            },
        ]
        audit.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        from rotation_shadow_report import build_hold_protected_set
        result = build_hold_protected_set(audit, date(2026, 5, 12))
        assert "GS" in result
        assert "JPM" not in result

    def test_tolerates_malformed_lines(self, tmp_path):
        audit = tmp_path / "apex_decision_audit.jsonl"
        audit.write_text("not-json\n" + json.dumps(
            {"ts": "2026-05-12T14:00:00+00:00", "action": "HOLD", "symbol": "AAPL"}
        ) + "\n")
        from rotation_shadow_report import build_hold_protected_set
        result = build_hold_protected_set(audit, date(2026, 5, 12))
        assert "AAPL" in result  # good line still processed


# ─────────────────────────────────────────────────────────────────────────────
# TestHoldProtectedInCandidates
# ─────────────────────────────────────────────────────────────────────────────

class TestHoldProtectedInCandidates:

    def test_hold_protected_flag_in_candidate_output(self):
        blocked = {"symbol": "AVGO", "score": 80, "ts": None}
        book = [
            {"symbol": "AAPL", "score": 40, "entry_score": 40, "qty": 100,
             "entry": 180.0, "trade_type": "POSITION"},
            {"symbol": "MSFT", "score": 35, "entry_score": 35, "qty": 50,
             "entry": 300.0, "trade_type": "POSITION"},
        ]
        hold_protected = frozenset({"AAPL"})
        candidates = rsr.build_shadow_candidates(
            blocked, book, date(2026, 5, 12),
            pru_syms=set(),
            held_syms=frozenset(["AAPL", "MSFT"]),
            hold_protected_syms=hold_protected,
        )
        aapl = next((c for c in candidates if c["symbol"] == "AAPL"), None)
        msft = next((c for c in candidates if c["symbol"] == "MSFT"), None)
        assert aapl is not None
        assert aapl["hold_protected"] is True
        assert msft is not None
        assert msft["hold_protected"] is False

    def test_hold_protected_none_defaults_to_false(self):
        blocked = {"symbol": "AVGO", "score": 80, "ts": None}
        book = [
            {"symbol": "AAPL", "score": 40, "entry_score": 40, "qty": 100,
             "entry": 180.0, "trade_type": "POSITION"},
        ]
        candidates = rsr.build_shadow_candidates(
            blocked, book, date(2026, 5, 12),
            pru_syms=set(),
            held_syms=frozenset(["AAPL"]),
            hold_protected_syms=None,
        )
        assert candidates[0]["hold_protected"] is False


# ─────────────────────────────────────────────────────────────────────────────
# TestCandidateScoreFromJsonl
# ─────────────────────────────────────────────────────────────────────────────

class TestCandidateScoreFromJsonl:
    """
    When load_margin_blocks_jsonl returns blocks with candidate_score,
    run_report seeds sym_score_index from them, improving gap accuracy.
    """

    def test_candidate_score_seeds_sym_score_index(self, tmp_path):
        # Build a minimal repo structure
        data_dir = tmp_path / "data"
        obs_dir = data_dir / "rotation_observability"
        obs_dir.mkdir(parents=True)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (data_dir / "positions.json").write_text(json.dumps([
            {"symbol": "AAPL", "score": 40, "entry_score": 40, "qty": 100,
             "entry": 180.0, "open_time": "2026-05-12T09:30:00+00:00"},
        ]))

        # Write a margin block JSONL with candidate_score=75
        block_rec = {
            "ts": "2026-05-12T14:00:01+00:00",
            "symbol": "AVGO",
            "candidate_score": 75,
            "direction": "LONG",
            "exp_code": "max_positions",
            "exp_reason": "cap",
            "estimated_notional": 95000.0,
            "notional_is_estimate": True,
            "portfolio_value": 950000.0,
            "open_position_count": 5,
        }
        (obs_dir / "margin_blocks.jsonl").write_text(json.dumps(block_rec) + "\n")

        out_dir = tmp_path / "out"
        _text, data = rsr.run_report(date(2026, 5, 12), tmp_path, out_dir)

        # section_1 returns "rows" (one per unique blocked symbol)
        s1 = data.get("section_1", {})
        blocked_list = s1.get("rows") or []
        avgo = next((b for b in blocked_list if b.get("symbol") == "AVGO"), None)
        assert avgo is not None, "AVGO should appear in blocked candidates"
        assert avgo.get("score") == 75, (
            f"candidate_score from JSONL should seed score=75, got {avgo.get('score')}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TestThreadSafety
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadSafety:

    def test_concurrent_writes_do_not_corrupt(self, tmp_path, monkeypatch):
        obs_dir = tmp_path / "rotation_observability"
        obs_dir.mkdir()

        # Patch module paths ONCE before threads start — avoids per-thread global mutation
        monkeypatch.setattr(ro, "_OBS_DIR", str(obs_dir))
        monkeypatch.setattr(ro, "_BLOCKS_PATH", str(obs_dir / "margin_blocks.jsonl"))
        monkeypatch.setattr(ro, "_SNAPSHOTS_PATH", str(obs_dir / "position_snapshots.jsonl"))

        errors: list[str] = []

        def _worker(sym: str) -> None:
            try:
                ro.write_margin_block(
                    symbol=sym,
                    candidate_score=60,
                    direction="LONG",
                    exp_code="max_positions",
                    exp_reason="cap",
                    estimated_notional=90000.0,
                    portfolio_value=900000.0,
                    open_position_count=10,
                    max_positions=10,
                    max_alloc_pct=1.0,
                    max_single_pct=0.10,
                    active_trades=None,
                )
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=_worker, args=(f"SYM{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        records = _read_jsonl(obs_dir / "margin_blocks.jsonl")
        assert len(records) == 10
        # All records must be valid JSON (no corruption)
        syms = {r["symbol"] for r in records}
        assert len(syms) == 10
