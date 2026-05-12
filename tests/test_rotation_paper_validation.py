"""
tests/test_rotation_paper_validation.py — Unit tests for rotation_paper_validation.py.

Fixture-based only.  No live file I/O.  No trading runtime imports.
All file I/O goes through temporary directories.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from datetime import date, datetime, timezone

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

import rotation_paper_validation as rpv

UTC = timezone.utc

# ── Fixture helpers ───────────────────────────────────────────────────────────

def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _block(
    symbol: str = "DVA",
    score: int = 75,
    exp_code: str = "margin_gross_cap_block",
    ts_str: str = "2026-05-12T17:03:48+00:00",
    estimated_notional: float = 57_000.0,
    portfolio_value: float = 950_000.0,
    notional_is_estimate: bool = True,
) -> dict:
    b = {
        "ts": ts_str,
        "symbol": symbol,
        "candidate_score": score,
        "direction": "LONG",
        "exp_code": exp_code,
        "exp_reason": "Margin gross cap exceeded",
        "estimated_notional": estimated_notional,
        "notional_is_estimate": notional_is_estimate,
        "portfolio_value": portfolio_value,
        "open_position_count": 17,
        "max_positions": 100,
        "max_alloc_pct": 1.0,
        "max_single_pct": 0.06,
        "_ts": _ts(ts_str),
    }
    return b


def _snapshot(
    ts_str: str = "2026-05-12T17:03:48+00:00",
    trigger: str = "margin_block:DVA",
    positions: dict | None = None,
) -> dict:
    if positions is None:
        positions = _default_book()
    return {
        "ts": ts_str,
        "_ts": _ts(ts_str),
        "trigger": trigger,
        "positions": positions,
    }


def _default_book() -> dict:
    """Book with 3 positions below 50, 2 below 35 — qualifies for paper validation."""
    return {
        "AAPL": {"symbol": "AAPL", "score": 85, "qty": 200, "entry": 290.0,
                 "open_time": "2026-05-07T10:00:00+00:00", "trade_type": "POSITION", "direction": "LONG", "pnl": 1000},
        "MSFT": {"symbol": "MSFT", "score": 63, "qty": 130, "entry": 420.0,
                 "open_time": "2026-05-07T11:00:00+00:00", "trade_type": "POSITION", "direction": "LONG", "pnl": -200},
        "WDC":  {"symbol": "WDC",  "score": 27, "qty": 120, "entry": 480.0,
                 "open_time": "2026-05-08T20:00:00+00:00", "trade_type": "POSITION", "direction": "LONG", "pnl": -100},
        "XLK":  {"symbol": "XLK",  "score": 26, "qty": 325, "entry": 175.0,
                 "open_time": "2026-05-11T09:00:00+00:00", "trade_type": "SWING",    "direction": "LONG", "pnl": -300},
        "XLE":  {"symbol": "XLE",  "score": 23, "qty": 540, "entry": 180.0,
                 "open_time": "2026-05-12T11:00:00+00:00", "trade_type": "SWING",    "direction": "LONG", "pnl": -400},
        "IWM":  {"symbol": "IWM",  "score": 83, "qty": 200, "entry": 287.0,
                 "open_time": "2026-05-11T14:00:00+00:00", "trade_type": "SWING",    "direction": "LONG", "pnl": -500},
        "PWR":  {"symbol": "PWR",  "score": 74, "qty": 155, "entry": 360.0,
                 "open_time": "2026-05-11T16:00:00+00:00", "trade_type": "SWING",    "direction": "LONG", "pnl": 200},
    }


def _write_jsonl(path: pathlib.Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


# ── Test 1: qualifying margin block ──────────────────────────────────────────

def test_margin_block_qualifies_when_score_and_gap_meet_threshold():
    block = _block(symbol="DVA", score=75, exp_code="margin_gross_cap_block")
    snapshots = [_snapshot()]
    opp, reason = rpv.qualify_block(block, snapshots, min_blocked_score=70, min_gap_vs_book=20)
    assert opp is not None, f"Expected opportunity, got skip: {reason}"
    assert opp["blocked_symbol"] == "DVA"
    assert opp["blocked_score"] == 75
    assert opp["gap_vs_book"] > 20


# ── Test 2: account_values_stale_block excluded ───────────────────────────────

def test_account_values_stale_block_excluded():
    block = _block(symbol="SLB", score=80, exp_code="account_values_stale_block")
    snapshots = [_snapshot()]
    opp, reason = rpv.qualify_block(block, snapshots, min_blocked_score=70, min_gap_vs_book=20)
    assert opp is None
    assert "account_values_stale_block" in reason


# ── Test 3: spread_block excluded ─────────────────────────────────────────────

def test_spread_block_excluded():
    block = _block(symbol="NVDA", score=90, exp_code="spread_block")
    snapshots = [_snapshot()]
    opp, reason = rpv.qualify_block(block, snapshots, min_blocked_score=70, min_gap_vs_book=20)
    assert opp is None
    assert "spread_block" in reason


# ── Test 4: missing snapshot returns skipped reason ──────────────────────────

def test_missing_snapshot_returns_skip_reason():
    block = _block(symbol="DVA", score=75, ts_str="2026-05-12T18:00:00+00:00")
    # No snapshots at all — find_snapshot_at returns None
    snapshots: list = []
    opp, reason = rpv.qualify_block(block, snapshots, min_blocked_score=70, min_gap_vs_book=20)
    assert opp is None
    assert "snapshot" in reason.lower()


# ── Test 5: shadow exits ranked deterministically ────────────────────────────

def test_shadow_exits_ranked_deterministically():
    book = _default_book()
    session_date = date(2026, 5, 12)
    ranks1 = rpv.rank_shadow_exits(book, 75.0, session_date)
    ranks2 = rpv.rank_shadow_exits(book, 75.0, session_date)
    symbols1 = [c["symbol"] for c in ranks1]
    symbols2 = [c["symbol"] for c in ranks2]
    assert symbols1 == symbols2, "Ranking must be deterministic"


# ── Test 6: top 1, 2, 3 scenarios generated ──────────────────────────────────

def test_scenarios_a_b_c_generated():
    block = _block()
    snapshots = [_snapshot()]
    opp, _ = rpv.qualify_block(block, snapshots, min_blocked_score=70, min_gap_vs_book=20)
    assert opp is not None
    scenarios = rpv.build_scenarios(opp, {}, lookahead_hours=24, max_shadow_exits=3)
    labels = [s["scenario"] for s in scenarios]
    assert "A" in labels
    assert "B" in labels
    assert "C" in labels
    assert scenarios[0]["exit_set_size"] == 1
    assert scenarios[1]["exit_set_size"] == 2
    assert scenarios[2]["exit_set_size"] == 3


# ── Test 7: estimated_notional is labelled as estimate ───────────────────────

def test_estimated_notional_labelled_as_estimate():
    block = _block(notional_is_estimate=True)
    snapshots = [_snapshot()]
    opp, _ = rpv.qualify_block(block, snapshots, min_blocked_score=70, min_gap_vs_book=20)
    scenarios = rpv.build_scenarios(opp, {}, lookahead_hours=24, max_shadow_exits=1)
    assert scenarios[0]["notional_is_estimate"] is True
    assert scenarios[0]["estimated_notional"] == pytest.approx(57_000.0)


# ── Test 8: capacity_sufficient_estimated calculated correctly ────────────────

def test_capacity_sufficient_estimated():
    block = _block(estimated_notional=50_000.0)
    snapshots = [_snapshot()]
    opp, _ = rpv.qualify_block(block, snapshots, min_blocked_score=70, min_gap_vs_book=20)
    scenarios = rpv.build_scenarios(opp, {}, lookahead_hours=24, max_shadow_exits=3)
    # Top 1 exit: XLE 540 × 180 = $97,200 > $50K → sufficient
    scenario_a = next(s for s in scenarios if s["scenario"] == "A")
    assert scenario_a["capacity_sufficient_estimated"] is True
    # Verify cap_released matches top-1 notional
    top1 = scenario_a["shadow_exit_candidates"][0]
    assert scenario_a["theoretical_capacity_released"] == pytest.approx(top1["notional"])


# ── Test 9: OUTCOME_PENDING fires when future outcome data unavailable ────────

def test_outcome_pending_when_no_training_data():
    block = _block()
    snapshots = [_snapshot()]
    opp, _ = rpv.qualify_block(block, snapshots, min_blocked_score=70, min_gap_vs_book=20)
    scenarios = rpv.build_scenarios(opp, {}, lookahead_hours=24, max_shadow_exits=3)
    for s in scenarios:
        assert s["outcome_status"] == "OUTCOME_PENDING"
        assert s["actual_outcome_available"] is False
        assert s["relative_uplift"] is None


# ── Test 10: PAPER_VALIDATION_PENDING_OUTCOMES verdict ───────────────────────

def test_verdict_pending_outcomes():
    # All scenarios have OUTCOME_PENDING
    block = _block()
    snapshots = [_snapshot()]
    opp, _ = rpv.qualify_block(block, snapshots, min_blocked_score=70, min_gap_vs_book=20)
    scenarios = rpv.build_scenarios(opp, {}, lookahead_hours=24, max_shadow_exits=3)
    verdict, action = rpv.compute_validation_verdict(scenarios)
    assert verdict == "PAPER_VALIDATION_PENDING_OUTCOMES"
    assert action == "KEEP_RUNNING_PAPER_VALIDATION"


# ── Test 11: PAPER_VALIDATION_NO_OPPORTUNITIES verdict ───────────────────────

def test_verdict_no_opportunities_on_empty_scenarios():
    verdict, action = rpv.compute_validation_verdict([])
    assert verdict == "PAPER_VALIDATION_NO_OPPORTUNITIES"
    assert action == "KEEP_RUNNING_PAPER_VALIDATION"


# ── Test 12: PAPER_VALIDATION_INSUFFICIENT_DATA verdict ──────────────────────

def test_verdict_insufficient_data():
    # Manufacture a scenario with outcome_status neither PENDING nor AVAILABLE
    fake_scenario = {
        "scenario": "A",
        "outcome_status": "INSUFFICIENT_DATA",
        "actual_outcome_available": False,
        "relative_uplift": None,
        "live_action_permitted": False,
    }
    verdict, _ = rpv.compute_validation_verdict([fake_scenario])
    assert verdict == "PAPER_VALIDATION_INSUFFICIENT_DATA"


# ── Test 13: live_rotation_allowed always false ───────────────────────────────

def test_live_rotation_allowed_always_false():
    with tempfile.TemporaryDirectory() as tmpdir:
        obs = pathlib.Path(tmpdir) / "obs"
        obs.mkdir()
        out = pathlib.Path(tmpdir) / "out"
        report = rpv.main([
            "--since", "2026-05-12",
            "--output-dir", str(out),
        ])
    assert report["live_rotation_allowed"] is False


# ── Test 14: broker_connected always false ────────────────────────────────────

def test_broker_connected_always_false():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = pathlib.Path(tmpdir) / "out"
        report = rpv.main([
            "--since", "2026-05-12",
            "--output-dir", str(out),
        ])
    assert report["broker_connected"] is False


# ── Test 15: order_generation_allowed always false ───────────────────────────

def test_order_generation_allowed_always_false():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = pathlib.Path(tmpdir) / "out"
        report = rpv.main([
            "--since", "2026-05-12",
            "--output-dir", str(out),
        ])
    assert report["order_generation_allowed"] is False


# ── Test 16: malformed JSONL tolerated ───────────────────────────────────────

def test_malformed_jsonl_tolerated():
    with tempfile.TemporaryDirectory() as tmpdir:
        obs = pathlib.Path(tmpdir) / "obs"
        obs.mkdir()
        blocks_path = obs / "margin_blocks.jsonl"
        blocks_path.write_text(
            '{"ts": "not-a-date", "symbol": "BAD", "candidate_score": 75}\n'
            '{"this is": "not json at all\n'
            '{"ts": "2026-05-12T17:03:48+00:00", "symbol": "DVA", '
            '"candidate_score": 75, "exp_code": "margin_gross_cap_block", '
            '"estimated_notional": 57000, "portfolio_value": 950000, '
            '"notional_is_estimate": true, "open_position_count": 17}\n',
            encoding="utf-8",
        )
        records, malformed = rpv.load_margin_blocks(blocks_path, date(2026, 5, 12))
        # Two lines are bad; only the well-formed line with a parseable ts should load
        assert malformed >= 1
        # No crash — function returns gracefully


# ── Test 17: missing files do not crash ──────────────────────────────────────

def test_missing_files_do_not_crash():
    with tempfile.TemporaryDirectory() as tmpdir:
        obs = pathlib.Path(tmpdir) / "obs"
        obs.mkdir()
        # Neither margin_blocks.jsonl nor position_snapshots.jsonl exist
        blocks, bad_b = rpv.load_margin_blocks(obs / "margin_blocks.jsonl", date(2026, 5, 12))
        snaps,  bad_s = rpv.load_position_snapshots(obs / "position_snapshots.jsonl")
        training = rpv.load_training_records(obs / "training_records.jsonl")
        assert blocks == []
        assert snaps == []
        assert dict(training) == {}
        assert bad_b == 0
        assert bad_s == 0


# ── Test 18: no runtime trading modules imported ─────────────────────────────

def test_no_runtime_trading_modules_imported():
    """
    Verify that rotation_paper_validation does not directly import any execution-path
    trading runtime module.  We check the module's own __dict__ (direct imports only)
    rather than sys.modules, which is polluted by pytest collecting other tests.
    """
    forbidden = {
        "orders_core", "bot_trading", "bot_ibkr", "orders_state",
        "signal_dispatcher", "position_sizer", "smart_execution",
        "risk_manager", "guardrails", "market_intelligence",
        "apex_orchestrator", "bot_dashboard", "sentinel_agents",
    }
    import importlib
    mod = importlib.import_module("rotation_paper_validation")
    # Only check what the module itself imported into its namespace
    for name in forbidden:
        assert name not in mod.__dict__, (
            f"Execution-path module '{name}' found in rotation_paper_validation namespace"
        )
    # Verify the source file has no 'import <forbidden>' lines as a belt-and-suspenders check
    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for name in forbidden:
        assert f"import {name}" not in src, (
            f"'import {name}' found in rotation_paper_validation.py source"
        )


# ── Additional integration test: full pipeline via main() ────────────────────

def test_main_produces_report_with_pending_outcomes(tmp_path):
    obs_dir = tmp_path / "rotation_observability"
    obs_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "out"

    # Write a qualifying margin block
    block_record = {
        "ts": "2026-05-12T17:03:48+00:00",
        "symbol": "DVA",
        "candidate_score": 75,
        "direction": "LONG",
        "exp_code": "margin_gross_cap_block",
        "exp_reason": "Margin cap exceeded",
        "estimated_notional": 57000.0,
        "notional_is_estimate": True,
        "portfolio_value": 950000.0,
        "open_position_count": 17,
        "max_positions": 100,
        "max_alloc_pct": 1.0,
        "max_single_pct": 0.06,
    }
    snapshot_record = {
        "ts": "2026-05-12T17:03:48+00:00",
        "trigger": "margin_block:DVA",
        "positions": _default_book(),
    }

    _write_jsonl(obs_dir / "margin_blocks.jsonl", [block_record])
    _write_jsonl(obs_dir / "position_snapshots.jsonl", [snapshot_record])

    import unittest.mock as mock
    # Redirect _repo_root to use tmp_path
    original_root = rpv._repo_root
    with mock.patch.object(rpv, "_repo_root", return_value=tmp_path):
        # Also need data dir to exist
        (tmp_path / "data" / "rotation_observability").mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy(obs_dir / "margin_blocks.jsonl",
                    tmp_path / "data" / "rotation_observability" / "margin_blocks.jsonl")
        shutil.copy(obs_dir / "position_snapshots.jsonl",
                    tmp_path / "data" / "rotation_observability" / "position_snapshots.jsonl")

        report = rpv.main([
            "--since", "2026-05-12",
            "--output-dir", str(out_dir),
            "--lookahead-hours", "24",
        ])

    assert report["live_rotation_allowed"] is False
    assert report["broker_connected"] is False
    assert report["order_generation_allowed"] is False
    assert report["opportunities_detected"] >= 1
    assert len(report["scenarios"]) >= 3
    assert report["verdict"] == "PAPER_VALIDATION_PENDING_OUTCOMES"
    for s in report["scenarios"]:
        assert s["live_action_permitted"] is False
        assert s["outcome_status"] == "OUTCOME_PENDING"
        assert s["notional_is_estimate"] is True

    # Verify artifacts were written
    artifacts = list(out_dir.glob("report_*.json"))
    assert artifacts, "Expected JSON report artifact"
    txt_artifacts = list(out_dir.glob("report_*.txt"))
    assert txt_artifacts, "Expected TXT report artifact"
