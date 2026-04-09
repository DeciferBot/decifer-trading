"""Tests for lightweight_cycle_check() and _regime_polarity() — GAP-001."""

import sys
import os
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portfolio_manager import lightweight_cycle_check, _regime_polarity


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pos(symbol="AAPL", trade_type="HOLD", entry_regime="BULL_TRENDING",
         mins_ago=200, entry=100.0, current=100.5):
    open_time = (datetime.now(timezone.utc) - timedelta(minutes=mins_ago)).isoformat()
    return {
        "symbol":     symbol,
        "trade_type": trade_type,
        "regime":     entry_regime,
        "open_time":  open_time,
        "entry":      entry,
        "current":    current,
    }


def _regime(label="BULL_TRENDING"):
    return {"regime": label}


# ── _regime_polarity ──────────────────────────────────────────────────────────

def test_polarity_bull():
    assert _regime_polarity("BULL_TRENDING") == "BULL"
    assert _regime_polarity("BULL") == "BULL"

def test_polarity_bear():
    assert _regime_polarity("BEAR") == "BEAR"
    assert _regime_polarity("BEAR_TRENDING") == "BEAR"

def test_polarity_neutral_and_unknown():
    assert _regime_polarity("NEUTRAL") == ""
    assert _regime_polarity("UNKNOWN") == ""
    assert _regime_polarity("") == ""
    assert _regime_polarity(None) == ""


# ── HOLD: polar flip triggers REVIEW ─────────────────────────────────────────

def test_hold_review_on_bull_to_bear():
    pos = _pos(trade_type="HOLD", entry_regime="BULL_TRENDING")
    actions = lightweight_cycle_check([pos], _regime("BEAR"), [])
    assert len(actions) == 1
    a = actions[0]
    assert a["symbol"] == "AAPL"
    assert a["action"] == "REVIEW"
    assert "BULL_TRENDING" in a["reasoning"]
    assert "BEAR" in a["reasoning"]
    assert "polar" in a["reasoning"].lower()


def test_hold_review_on_bear_to_bull():
    pos = _pos(trade_type="HOLD", entry_regime="BEAR")
    actions = lightweight_cycle_check([pos], _regime("BULL_TRENDING"), [])
    assert len(actions) == 1
    assert actions[0]["action"] == "REVIEW"


# ── HOLD: same polarity — no action ──────────────────────────────────────────

def test_hold_no_action_same_polarity():
    # BULL_TRENDING → BULL is same polarity, not a flip
    pos = _pos(trade_type="HOLD", entry_regime="BULL_TRENDING")
    actions = lightweight_cycle_check([pos], _regime("BULL"), [])
    assert actions == []


def test_hold_no_action_when_entry_regime_unknown():
    pos = _pos(trade_type="HOLD", entry_regime="UNKNOWN")
    actions = lightweight_cycle_check([pos], _regime("BEAR"), [])
    assert actions == []


def test_hold_no_action_when_current_regime_unknown():
    pos = _pos(trade_type="HOLD", entry_regime="BULL")
    actions = lightweight_cycle_check([pos], _regime("UNKNOWN"), [])
    assert actions == []


def test_hold_no_action_when_current_regime_neutral():
    # BULL → NEUTRAL is not a polar flip (no BEAR signal)
    pos = _pos(trade_type="HOLD", entry_regime="BULL_TRENDING")
    actions = lightweight_cycle_check([pos], _regime("NEUTRAL"), [])
    assert actions == []


# ── HOLD with empty entry_regime field ───────────────────────────────────────

def test_hold_no_action_when_entry_regime_empty():
    pos = _pos(trade_type="HOLD", entry_regime="")
    actions = lightweight_cycle_check([pos], _regime("BEAR"), [])
    assert actions == []


# ── Regression: SCALP and SWING behaviour unchanged ──────────────────────────

def test_scalp_still_exits_when_stale():
    pos = _pos(trade_type="SCALP", entry_regime="BULL", mins_ago=100,
               entry=100.0, current=100.1)  # pnl < 0.3%
    actions = lightweight_cycle_check([pos], _regime("BULL"), [])
    assert len(actions) == 1
    assert actions[0]["action"] == "EXIT"


def test_swing_still_reviews_on_regime_change():
    pos = _pos(trade_type="SWING", entry_regime="BULL", mins_ago=60)
    actions = lightweight_cycle_check([pos], _regime("BEAR"), [])
    assert len(actions) == 1
    assert actions[0]["action"] == "REVIEW"
    assert "SWING" in actions[0]["reasoning"]


def test_empty_positions_returns_empty():
    assert lightweight_cycle_check([], _regime("BULL"), []) == []
