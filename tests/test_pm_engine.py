"""
tests/test_pm_engine.py — Acceptance tests for the Portfolio Management Engine.

Covers all 11 acceptance tests from the migration spec plus 5 safety rail
unit tests and 2 structural/import-guard tests.

All tests use only in-process mocks — no IBKR, no Alpaca, no filesystem.
"""
from __future__ import annotations

import datetime
import importlib
import json
import pathlib
import sys
import types
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

UTC = datetime.timezone.utc


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _now_iso(hours_ago: float = 0.0) -> str:
    t = datetime.datetime.now(UTC) - datetime.timedelta(hours=hours_ago)
    return t.isoformat()


def _pos(
    symbol: str = "AAPL",
    qty: float = 100,
    entry: float = 100.0,
    current: float = 100.0,
    pnl: float = 0.0,
    entry_score: float = 45.0,
    open_hours_ago: float = 10.0,
    hold_protected: bool = False,
    status: str = "OPEN",
) -> dict:
    return {
        "symbol":      symbol,
        "qty":         qty,
        "entry":       entry,
        "current":     current,
        "pnl":         pnl,
        "entry_score": entry_score,
        "score":       entry_score,
        "open_time":   _now_iso(open_hours_ago),
        "status":      status,
        "hold_protected": hold_protected,
        "trade_type":  "MOMENTUM",
        "direction":   "LONG",
    }


@pytest.fixture(autouse=True)
def _mock_runtime_imports(monkeypatch):
    """Stub heavy runtime deps so tests never touch IBKR or Alpaca."""
    # bot_state
    bs = types.ModuleType("bot_state")
    bs.ib = MagicMock()
    bs.account_values = {"NetLiquidation": "100000"}
    bs.account_values_updated_at = __import__("time").time()
    bs.active_trades = {}
    monkeypatch.setitem(sys.modules, "bot_state", bs)

    # alpaca_stream — fresh quote, zero spread
    as_mod = types.ModuleType("alpaca_stream")

    class _QC:
        def get(self, sym):
            return {"spread_pct": 0.001, "ts": __import__("time").time()}
        def get_spread_pct(self, sym):
            return 0.001

    as_mod.QUOTE_CACHE = _QC()
    monkeypatch.setitem(sys.modules, "alpaca_stream", as_mod)

    # orders_core
    oc = types.ModuleType("orders_core")
    oc.execute_sell = MagicMock(return_value=True)
    monkeypatch.setitem(sys.modules, "orders_core", oc)

    # config
    cfg_mod = types.ModuleType("config")
    cfg_mod.CONFIG = {
        "ENABLE_PM_ENGINE":          True,
        "PM_MAX_ACTIONS_PER_DAY":    99,
        "PM_MAX_ACTION_NLV_PCT":     0.02,
        "PM_MIN_ACTION_NOTIONAL":    100.0,
        "PM_MIN_HOLD_HOURS":         2.0,
        "PM_COOLDOWN_HOURS":         0.0,   # disabled so action logic can be tested
        "PM_MAX_SPREAD_PCT":         0.01,
        "PM_ACCOUNT_MAX_AGE_S":      300.0,
        "PM_QUOTE_MAX_AGE_S":        30.0,
        "PM_MIN_ROTATE_ADVANTAGE":   10,
        "PM_OVERSIZE_THRESHOLD":     0.06,
        "PM_DEFAULT_TRIM_PCT":       0.33,
        "PM_TARGET_POSITION_PCT":    0.04,
        "PM_TRANSACTION_COST_PCT":   0.001,
        "PM_THESIS_DECAY_DELTA":     -10,
        "PM_THESIS_BROKEN_DELTA":    -15,
        "PM_THESIS_BROKEN_LOSS_PCT": -0.08,
    }
    monkeypatch.setitem(sys.modules, "config", cfg_mod)

    # Reload pm modules to pick up fresh stubs
    for mod_name in ("pm_thesis", "pm_rails", "pm_engine"):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])

    yield


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_pm_position(
    symbol="AAPL",
    market_value=5000.0,
    position_pct_nlv=0.05,
    unrealised_pnl_pct=0.0,
    holding_period_hours=10.0,
    entry_score=45.0,
    current_score=45.0,
    score_delta=0.0,
    thesis_status=None,
    spread_pct=0.001,
    quote_age_s=5.0,
    qty=50.0,
    entry_price=100.0,
    current_price=100.0,
):
    from pm_thesis import PMPosition, ThesisStatus
    if thesis_status is None:
        thesis_status = ThesisStatus.INTACT
    return PMPosition(
        symbol=symbol,
        market_value=market_value,
        position_pct_nlv=position_pct_nlv,
        unrealised_pnl_pct=unrealised_pnl_pct,
        holding_period_hours=holding_period_hours,
        entry_score=entry_score,
        current_score=current_score,
        score_delta=score_delta,
        thesis_status=thesis_status,
        spread_pct=spread_pct,
        quote_age_s=quote_age_s,
        qty=qty,
        entry_price=entry_price,
        current_price=current_price,
    )


# ── Acceptance test 1 & 2: G7 fix — partial trim not blocked by full position ─

def test_partial_trim_not_blocked_by_full_position_notional():
    """
    NLV=980k, position=60k (6.1% NLV), max action=2% NLV=19.6k.
    Old G7 blocked because 60k > 19.6k.
    New rail 7: proposed trim notional = 60k * 0.33 = 19.8k → just over cap.
    Use a smaller trim (15k) to confirm it passes.
    """
    import pm_rails
    from pm_engine import ActionType, PMAction

    nlv = 980_000.0
    cfg = {
        "ENABLE_PM_ENGINE":         True,
        "PM_MAX_ACTIONS_PER_DAY":   99,
        "PM_MAX_ACTION_NLV_PCT":    0.02,      # 2% NLV = 19,600
        "PM_MIN_ACTION_NOTIONAL":   100.0,
        "PM_MIN_HOLD_HOURS":        2.0,
        "PM_COOLDOWN_HOURS":        0.0,
        "PM_MAX_SPREAD_PCT":        0.01,
        "PM_ACCOUNT_MAX_AGE_S":     300.0,
        "PM_QUOTE_MAX_AGE_S":       30.0,
        "PM_TRANSACTION_COST_PCT":  0.001,
    }

    trim_action = PMAction(
        action_type=ActionType.TRIM,
        symbol="TSLA",
        proposed_notional=15_000.0,   # 15k proposed < 19.6k cap → should PASS
        action_score=30.0,
        rationale="trim test",
        trigger="margin_cap_block",
        holding_period_hours=10.0,
    )

    result = pm_rails.apply(trim_action, nlv, cfg)
    assert not result.safety_blocked, (
        f"Rail 7 should NOT block a 15k trim on a 60k position (NLV cap = 19.6k). "
        f"Blocked: {result.safety_block_reason}"
    )


def test_full_position_notional_does_not_block_trim():
    """
    Explicitly confirm: rail 7 checks proposed_notional, not market_value.
    Full position is 60k > 19.6k NLV cap. Proposed trim is 10k < cap. Must pass.
    """
    import pm_rails
    from pm_engine import ActionType, PMAction

    nlv = 980_000.0
    cfg = {
        "ENABLE_PM_ENGINE":         True,
        "PM_MAX_ACTIONS_PER_DAY":   99,
        "PM_MAX_ACTION_NLV_PCT":    0.02,
        "PM_MIN_ACTION_NOTIONAL":   100.0,
        "PM_MIN_HOLD_HOURS":        2.0,
        "PM_COOLDOWN_HOURS":        0.0,
        "PM_MAX_SPREAD_PCT":        0.01,
        "PM_ACCOUNT_MAX_AGE_S":     300.0,
        "PM_QUOTE_MAX_AGE_S":       30.0,
        "PM_TRANSACTION_COST_PCT":  0.001,
    }

    action = PMAction(
        action_type=ActionType.TRIM,
        symbol="TSLA",
        proposed_notional=10_000.0,   # 10k < 19.6k cap
        action_score=30.0,
        rationale="partial trim",
        trigger="scan_cycle",
        holding_period_hours=20.0,
    )

    result = pm_rails.apply(action, nlv, cfg)
    assert not result.safety_blocked, (
        f"10k proposed notional must pass a 19.6k cap. "
        f"Blocked reason: {result.safety_block_reason}"
    )


# ── Acceptance test 3: all action types generatable ───────────────────────────

def test_all_action_types_generatable():
    """HOLD, ADD, DCA, TRIM, FULL_EXIT, ROTATE, DO_NOTHING can all be generated."""
    import pm_engine
    from pm_engine import ActionType
    from pm_thesis import ThesisStatus

    # FULL_EXIT — broken thesis
    pos_broken = _build_pm_position(
        thesis_status=ThesisStatus.BROKEN,
        score_delta=-20.0,
        unrealised_pnl_pct=-0.10,
        holding_period_hours=24.0,
    )
    actions_broken = pm_engine._generate_actions(pos_broken, None, "scan_cycle", sys.modules["config"].CONFIG)
    assert any(a.action_type == ActionType.FULL_EXIT for a in actions_broken), "FULL_EXIT not generated for BROKEN thesis"

    # TRIM — decaying thesis
    pos_decay = _build_pm_position(thesis_status=ThesisStatus.DECAYING, score_delta=-12.0, holding_period_hours=20.0)
    actions_decay = pm_engine._generate_actions(pos_decay, None, "scan_cycle", sys.modules["config"].CONFIG)
    assert any(a.action_type == ActionType.TRIM for a in actions_decay), "TRIM not generated for DECAYING thesis"

    # HOLD — intact thesis
    pos_intact = _build_pm_position(thesis_status=ThesisStatus.INTACT, holding_period_hours=20.0)
    actions_intact = pm_engine._generate_actions(pos_intact, None, "scan_cycle", sys.modules["config"].CONFIG)
    assert any(a.action_type == ActionType.HOLD for a in actions_intact), "HOLD not generated for INTACT thesis"

    # DCA — intact thesis, in loss
    pos_dca = _build_pm_position(thesis_status=ThesisStatus.INTACT, unrealised_pnl_pct=-0.04, holding_period_hours=20.0)
    actions_dca = pm_engine._generate_actions(pos_dca, None, "scan_cycle", sys.modules["config"].CONFIG)
    assert any(a.action_type == ActionType.DCA for a in actions_dca), "DCA not generated for INTACT+loss"

    # ADD — strengthening, undersized
    pos_add = _build_pm_position(
        thesis_status=ThesisStatus.STRENGTHENING,
        position_pct_nlv=0.02,   # < PM_TARGET_POSITION_PCT=0.04
        unrealised_pnl_pct=0.03,
        score_delta=7.0,
        holding_period_hours=20.0,
    )
    actions_add = pm_engine._generate_actions(pos_add, None, "scan_cycle", sys.modules["config"].CONFIG)
    assert any(a.action_type == ActionType.ADD for a in actions_add), "ADD not generated for STRENGTHENING+undersized"

    # ROTATE — margin_cap_block trigger, clear advantage
    best = {"symbol": "NVDA", "score": 70}
    pos_rotate = _build_pm_position(current_score=45.0, holding_period_hours=20.0)
    actions_rotate = pm_engine._generate_actions(pos_rotate, best, "margin_cap_block", sys.modules["config"].CONFIG)
    assert any(a.action_type == ActionType.ROTATE for a in actions_rotate), "ROTATE not generated for margin_cap_block"

    # DO_NOTHING — always present
    assert any(a.action_type == ActionType.DO_NOTHING for a in actions_intact), "DO_NOTHING always expected"


# ── Acceptance tests 4-6: safety rails block on data quality ─────────────────

def test_stale_quote_blocks_execution():
    import pm_rails
    from pm_engine import ActionType, PMAction

    # Override quote cache to return stale timestamp (35s ago)
    stale_time = __import__("time").time() - 35
    sys.modules["alpaca_stream"].QUOTE_CACHE = type("QC", (), {
        "get": lambda self, s: {"spread_pct": 0.001, "ts": stale_time},
        "get_spread_pct": lambda self, s: 0.001,
    })()

    importlib.reload(sys.modules["pm_thesis"])

    cfg = {**sys.modules["config"].CONFIG, "PM_QUOTE_MAX_AGE_S": 30.0, "PM_COOLDOWN_HOURS": 0.0}
    action = PMAction(
        action_type=ActionType.FULL_EXIT, symbol="AAPL",
        proposed_notional=5000.0, action_score=40.0,
        rationale="test", trigger="scan_cycle", holding_period_hours=10.0,
    )
    result = pm_rails.apply(action, 100_000.0, cfg)
    assert result.safety_blocked
    assert "quote_stale" in result.safety_block_reason


def test_bad_spread_blocks_execution():
    import pm_rails
    from pm_engine import ActionType, PMAction

    sys.modules["alpaca_stream"].QUOTE_CACHE = type("QC", (), {
        "get": lambda self, s: {"spread_pct": 0.05, "ts": __import__("time").time()},
        "get_spread_pct": lambda self, s: 0.05,
    })()
    importlib.reload(sys.modules["pm_thesis"])

    cfg = {**sys.modules["config"].CONFIG, "PM_MAX_SPREAD_PCT": 0.01, "PM_COOLDOWN_HOURS": 0.0}
    action = PMAction(
        action_type=ActionType.TRIM, symbol="AAPL",
        proposed_notional=2000.0, action_score=30.0,
        rationale="test", trigger="scan_cycle", holding_period_hours=10.0,
    )
    result = pm_rails.apply(action, 100_000.0, cfg)
    assert result.safety_blocked
    assert "spread" in result.safety_block_reason


def test_invalid_price_blocks_execution():
    """NLV = 0 triggers rail 6 (invalid_nlv)."""
    import pm_rails
    from pm_engine import ActionType, PMAction

    cfg = {**sys.modules["config"].CONFIG, "PM_COOLDOWN_HOURS": 0.0}
    action = PMAction(
        action_type=ActionType.FULL_EXIT, symbol="AAPL",
        proposed_notional=5000.0, action_score=40.0,
        rationale="test", trigger="scan_cycle", holding_period_hours=10.0,
    )
    result = pm_rails.apply(action, 0.0, cfg)   # NLV=0 → invalid
    assert result.safety_blocked
    assert "invalid_nlv" in result.safety_block_reason


# ── Acceptance test 7: excessive proposed action notional blocked ──────────────

def test_excessive_action_notional_blocked():
    """proposed_notional=25k > 2% of 100k NLV (=2k) → blocked by rail 7."""
    import pm_rails
    from pm_engine import ActionType, PMAction

    cfg = {**sys.modules["config"].CONFIG, "PM_MAX_ACTION_NLV_PCT": 0.02, "PM_COOLDOWN_HOURS": 0.0}
    action = PMAction(
        action_type=ActionType.TRIM, symbol="AAPL",
        proposed_notional=25_000.0,  # > 2k cap on 100k NLV
        action_score=30.0, rationale="test", trigger="scan_cycle",
        holding_period_hours=10.0,
    )
    result = pm_rails.apply(action, 100_000.0, cfg)
    assert result.safety_blocked
    assert "action_notional" in result.safety_block_reason


# ── Acceptance test 8: candidate advantage must exceed cost + churn ───────────

def test_candidate_advantage_must_exceed_churn_penalty():
    """
    ROTATE action with low holding_period_hours gets a -30 churn penalty.
    If candidate advantage (15pts) < churn_penalty (30pts), ROTATE should score
    below HOLD and not be selected as best action.
    """
    import pm_engine
    from pm_engine import ActionType
    from pm_thesis import ThesisStatus

    pos = _build_pm_position(
        thesis_status=ThesisStatus.INTACT,
        current_score=50.0,
        holding_period_hours=1.0,  # very new — churn penalty fires
    )
    best = {"symbol": "NVDA", "score": 65}  # advantage=15 < churn_penalty=30
    cfg = sys.modules["config"].CONFIG

    actions = pm_engine._generate_actions(pos, best, "margin_cap_block", cfg)
    rotate_actions = [a for a in actions if a.action_type == ActionType.ROTATE]
    hold_actions   = [a for a in actions if a.action_type == ActionType.HOLD]

    assert rotate_actions, "ROTATE should still be generated (blocked later by rails)"
    assert hold_actions, "HOLD should be generated"
    # ROTATE score must be lower than HOLD score when churn penalty > advantage
    best_rotate = max(a.action_score for a in rotate_actions)
    best_hold   = max(a.action_score for a in hold_actions)
    assert best_rotate < best_hold, (
        f"ROTATE score {best_rotate:.1f} should be < HOLD score {best_hold:.1f} "
        f"when churn penalty exceeds candidate advantage"
    )


# ── Acceptance test 9: old rotation module not imported by live runtime ───────

def test_rotation_live_v1_not_imported_by_live_runtime():
    """
    Parse orders_core.py and bot_trading.py — rotation_live_v1 must not appear.
    This test enforces the retirement hard rule.
    """
    repo = pathlib.Path(__file__).parent.parent
    production_files = [
        repo / "orders_core.py",
        repo / "bot_trading.py",
    ]
    for path in production_files:
        assert path.exists(), f"Expected production file missing: {path}"
        content = path.read_text(encoding="utf-8")
        assert "rotation_live_v1" not in content, (
            f"RETIRED MODULE 'rotation_live_v1' is still referenced in {path.name}. "
            f"Remove all references as part of the PM engine migration."
        )


# ── Acceptance test 10: dashboard no longer exposes Rotation as primary concept

def test_dashboard_tab_not_rotation():
    """
    Parse static/dashboard.html — 'Rotation Live V1' heading must not appear.
    """
    html_path = pathlib.Path(__file__).parent.parent / "static" / "dashboard.html"
    assert html_path.exists(), "dashboard.html not found"
    content = html_path.read_text(encoding="utf-8")
    assert "Rotation Live V1" not in content, (
        "'Rotation Live V1' heading still present in dashboard.html — tab rename incomplete."
    )


# ── Acceptance test 11: DO_NOTHING always survives rails ─────────────────────

def test_do_nothing_always_survives_rails():
    """DO_NOTHING passes every safety rail regardless of conditions."""
    import pm_rails
    from pm_engine import ActionType, PMAction

    # Worst-case conditions: stale quote, bad spread, near-zero NLV
    sys.modules["alpaca_stream"].QUOTE_CACHE = type("QC", (), {
        "get": lambda self, s: {"spread_pct": 0.99, "ts": __import__("time").time() - 999},
        "get_spread_pct": lambda self, s: 0.99,
    })()
    importlib.reload(sys.modules["pm_thesis"])

    cfg = {**sys.modules["config"].CONFIG, "ENABLE_PM_ENGINE": True}
    action = PMAction(
        action_type=ActionType.DO_NOTHING, symbol="AAPL",
        proposed_notional=None, action_score=5.0,
        rationale="no signal", trigger="scan_cycle",
        holding_period_hours=0.1,
    )
    result = pm_rails.apply(action, 100_000.0, cfg)
    assert not result.safety_blocked, "DO_NOTHING must never be blocked by safety rails"


# ── Thesis classification tests ────────────────────────────────────────────────

def test_thesis_broken_generates_full_exit():
    import pm_engine
    from pm_engine import ActionType
    from pm_thesis import ThesisStatus

    pos = _build_pm_position(
        thesis_status=ThesisStatus.BROKEN,
        score_delta=-18.0,
        unrealised_pnl_pct=-0.09,
        holding_period_hours=30.0,
    )
    actions = pm_engine._generate_actions(pos, None, "scan_cycle", sys.modules["config"].CONFIG)
    assert any(a.action_type == ActionType.FULL_EXIT for a in actions)


def test_thesis_strengthening_generates_add():
    import pm_engine
    from pm_engine import ActionType
    from pm_thesis import ThesisStatus

    pos = _build_pm_position(
        thesis_status=ThesisStatus.STRENGTHENING,
        position_pct_nlv=0.02,  # < PM_TARGET_POSITION_PCT
        unrealised_pnl_pct=0.03,
        score_delta=8.0,
        holding_period_hours=20.0,
    )
    actions = pm_engine._generate_actions(pos, None, "scan_cycle", sys.modules["config"].CONFIG)
    assert any(a.action_type == ActionType.ADD for a in actions)


def test_thesis_classification_rules():
    """Unit test the _classify function directly."""
    from pm_thesis import ThesisStatus, _classify

    assert _classify(0.0, 0.0, 0.0, 0.0)            == ThesisStatus.UNKNOWN
    assert _classify(40.0, -18.0, -0.09, 10.0)       == ThesisStatus.BROKEN
    assert _classify(40.0, -12.0, 0.0, 10.0)         == ThesisStatus.DECAYING
    assert _classify(40.0, -6.0, -0.05, 10.0)        == ThesisStatus.DECAYING   # pnl+delta combo
    assert _classify(40.0, 1.0, 0.01, 60.0)          == ThesisStatus.PLAYED_OUT
    assert _classify(40.0, 8.0, 0.03, 10.0)          == ThesisStatus.STRENGTHENING
    assert _classify(40.0, 2.0, 0.01, 10.0)          == ThesisStatus.INTACT


# ── Safety rail unit tests ─────────────────────────────────────────────────────

def test_churn_penalty_fires_under_min_hold():
    """Action score drops 30 pts when holding_period_hours < PM_MIN_HOLD_HOURS."""
    import pm_engine
    from pm_engine import ActionType
    from pm_thesis import ThesisStatus

    cfg_base = sys.modules["config"].CONFIG

    pos_old = _build_pm_position(thesis_status=ThesisStatus.DECAYING, score_delta=-12.0, holding_period_hours=10.0)
    pos_new = _build_pm_position(thesis_status=ThesisStatus.DECAYING, score_delta=-12.0, holding_period_hours=1.0)

    old_actions = pm_engine._generate_actions(pos_old, None, "scan_cycle", cfg_base)
    new_actions = pm_engine._generate_actions(pos_new, None, "scan_cycle", cfg_base)

    def best_score(actions, atype):
        filtered = [a for a in actions if a.action_type == atype]
        return max((a.action_score for a in filtered), default=None)

    old_trim = best_score(old_actions, ActionType.TRIM)
    new_trim = best_score(new_actions, ActionType.TRIM)
    assert old_trim is not None and new_trim is not None
    assert old_trim > new_trim, (
        f"Old position trim score {old_trim:.1f} should exceed new position score {new_trim:.1f} "
        f"(churn penalty not applied)"
    )


def test_decision_logged_for_every_position(tmp_path, monkeypatch):
    """One JSONL record is written per position evaluated."""
    import pm_engine

    # Redirect log file to tmp
    monkeypatch.setattr(pm_engine, "_DECISIONS_DIR", tmp_path)
    monkeypatch.setattr(pm_engine, "_DECISIONS_FILE", tmp_path / "decisions.jsonl")

    snapshot = {
        "AAPL": _pos("AAPL", open_hours_ago=10.0),
        "MSFT": _pos("MSFT", qty=50, entry=200.0, current=200.0, open_hours_ago=8.0),
    }
    sys.modules["bot_state"].account_values = {"NetLiquidation": "100000"}
    sys.modules["bot_state"].account_values_updated_at = __import__("time").time()

    pm_engine.evaluate(
        trigger="scan_cycle",
        active_trades_snapshot=snapshot,
    )

    log_file = tmp_path / "decisions.jsonl"
    assert log_file.exists(), "Decision log was not created"
    records = [json.loads(l) for l in log_file.read_text().strip().splitlines() if l]
    logged_symbols = {r["symbol"] for r in records}
    assert "AAPL" in logged_symbols
    assert "MSFT" in logged_symbols


def test_hypothetical_mode_no_execute_sell(monkeypatch):
    """When ENABLE_PM_ENGINE=False, execute_sell is never called."""
    import pm_engine

    sys.modules["config"].CONFIG = {
        **sys.modules["config"].CONFIG,
        "ENABLE_PM_ENGINE": False,
    }

    snapshot = {"AAPL": _pos("AAPL", open_hours_ago=30.0, pnl=-500.0, entry_score=25.0)}
    sys.modules["bot_state"].account_values = {"NetLiquidation": "100000"}
    sys.modules["bot_state"].account_values_updated_at = __import__("time").time()

    pm_engine.evaluate(trigger="scan_cycle", active_trades_snapshot=snapshot)

    sys.modules["orders_core"].execute_sell.assert_not_called()


def test_hypothetical_status_when_flag_off(tmp_path, monkeypatch):
    """
    When ENABLE_PM_ENGINE=False, every decision log record must show
    final_status='HYPOTHETICAL', never 'SAFETY_BLOCKED'.

    Root cause guarded: _log() previously treated the feature_flag_off rail
    block identically to true safety rail blocks. This resulted in every
    hypothetical-mode record showing SAFETY_BLOCKED, which is misleading
    (it implies something was wrong with the trade, not that the engine was
    simply not yet activated).
    """
    import pm_engine

    monkeypatch.setattr(pm_engine, "_DECISIONS_DIR", tmp_path)
    monkeypatch.setattr(pm_engine, "_DECISIONS_FILE", tmp_path / "decisions.jsonl")

    sys.modules["config"].CONFIG = {
        **sys.modules["config"].CONFIG,
        "ENABLE_PM_ENGINE": False,
        "PM_COOLDOWN_HOURS": 0.0,
    }
    sys.modules["bot_state"].account_values = {"NetLiquidation": "100000"}
    sys.modules["bot_state"].account_values_updated_at = __import__("time").time()

    # Position with BROKEN thesis — would trigger FULL_EXIT if live
    snapshot = {
        "AAPL": _pos("AAPL", qty=100, entry=100.0, current=88.0,
                     pnl=-1200.0, entry_score=45.0, open_hours_ago=20.0),
    }

    pm_engine.evaluate(trigger="scan_cycle", active_trades_snapshot=snapshot)

    log_file = tmp_path / "decisions.jsonl"
    assert log_file.exists(), "Decision log was not created"
    records = [json.loads(l) for l in log_file.read_text().strip().splitlines() if l]
    assert records, "No records written"

    bad = [r for r in records if r.get("final_status") == "SAFETY_BLOCKED"]
    assert not bad, (
        f"Expected all flag-off decisions to be HYPOTHETICAL, but found SAFETY_BLOCKED "
        f"records: {bad}"
    )

    hyp = [r for r in records if r.get("final_status") == "HYPOTHETICAL"]
    assert hyp, "Expected at least one HYPOTHETICAL record when flag is off"


def test_do_nothing_rationale_includes_thesis_context():
    """
    DO_NOTHING rationale must include thesis status, score delta, and PnL
    so the decision log is informative without needing to join other data.
    """
    from pm_engine import _do_nothing
    from pm_thesis import PMPosition, ThesisStatus

    pos = PMPosition(
        symbol="MSFT",
        market_value=5000.0,
        position_pct_nlv=0.05,
        unrealised_pnl_pct=-0.03,
        holding_period_hours=8.0,
        entry_score=50.0,
        current_score=47.0,
        score_delta=-3.0,
        thesis_status=ThesisStatus.INTACT,
        spread_pct=0.001,
        quote_age_s=5.0,
        qty=50.0,
        entry_price=100.0,
        current_price=97.0,
    )

    action = _do_nothing(pos)
    assert "THESIS_INTACT" in action.rationale, \
        f"DO_NOTHING rationale must include thesis status. Got: {action.rationale}"
    assert "-3" in action.rationale, \
        f"DO_NOTHING rationale must include score delta. Got: {action.rationale}"
    assert "%" in action.rationale, \
        f"DO_NOTHING rationale must include PnL percentage. Got: {action.rationale}"
