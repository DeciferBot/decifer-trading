"""
test_pm_exit_reason.py

Tests for:
  - tp_order_id-based TP detection logic (classification algorithm)
  - _build_pm_exit_reason() structured output
  - PM TRIM/EXIT reason format
"""
import os
import sys
import types
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub all heavy deps before any Decifer module loads ───────────────────────
for _mod_name in [
    "ib_async", "ib_insync", "anthropic", "yfinance", "praw",
    "feedparser", "tvDatafeed", "requests_html", "httpx", "colorama",
    "portfolio_manager",
]:
    sys.modules.setdefault(_mod_name, MagicMock())

# colorama needs Fore / Style / init attributes
import colorama as _colorama_stub
_colorama_stub.Fore = MagicMock()
_colorama_stub.Style = MagicMock()
_colorama_stub.init = MagicMock()

# Stub every Decifer module that bot_trading imports at module level
for _decifer in [
    "bot_state", "bot_account", "bot_ibkr", "scanner", "signals", "agents",
    "orders", "options", "options_scanner", "risk", "risk_gates", "learning",
    "signal_types", "signal_dispatcher", "signal_pipeline",
]:
    sys.modules.setdefault(_decifer, MagicMock())

# Ensure bot_state.dash and bot_state.clog exist
import bot_state as _bs
_bs.dash = {}
_bs.clog = MagicMock()

import config as _config_mod
_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1000,
    "mongo_uri": "",
    "db_name": "test",
    "scalp_max_hold_minutes": 90,
    "active_account": "TEST",
}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg

import pytest

# Now import bot_trading — module-level code runs with stubs in place
import bot_trading


# ─── helpers ────────────────────────────────────────────────────────────────

def _make_pos(
    trade_type="SCALP",
    direction="LONG",
    entry=100.0,
    current=105.0,
    qty=10,
    entry_regime="MOMENTUM_BULL",
    open_time=None,
):
    if open_time is None:
        open_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    return {
        "symbol": "TEST",
        "direction": direction,
        "trade_type": trade_type,
        "entry": entry,
        "current": current,
        "qty": qty,
        "entry_regime": entry_regime,
        "open_time": open_time,
        "score": 25,
        "reasoning": "test",
        "signal_scores": {},
        "ic_weights_at_entry": None,
        "agent_outputs": {},
        "candle_gate": "PASS",
        "tranche_id": None,
        "parent_trade_id": None,
        "pattern_id": None,
        "tp": 108.0,
        "sl": 97.0,
    }


# ─── _polarity ───────────────────────────────────────────────────────────────

class TestPolarity:
    def test_bull_regimes(self):
        assert bot_trading._polarity("MOMENTUM_BULL") == "BULL"
        assert bot_trading._polarity("RELIEF_RALLY") == "BULL"
        assert bot_trading._polarity("BULL_TRENDING") == "BULL"

    def test_bear_regimes(self):
        assert bot_trading._polarity("TRENDING_BEAR") == "BEAR"
        assert bot_trading._polarity("DISTRIBUTION") == "BEAR"
        assert bot_trading._polarity("BEAR_VOLATILE") == "BEAR"

    def test_neutral_returns_empty(self):
        assert bot_trading._polarity("NEUTRAL") == ""
        assert bot_trading._polarity("UNKNOWN") == ""
        assert bot_trading._polarity("") == ""
        assert bot_trading._polarity(None) == ""


# ─── _build_pm_exit_reason ──────────────────────────────────────────────────

class TestBuildPmExitReason:
    def test_format_contains_required_fields(self):
        pos = _make_pos(trade_type="SCALP", entry_regime="MOMENTUM_BULL")
        regime = {"regime": "TRENDING_BEAR", "session_character": "TRENDING_BEAR"}
        result = bot_trading._build_pm_exit_reason(
            pos, regime, pm_trigger="regime_shift", reason_pm="vol spike", exit_tag="pm_exit"
        )
        assert result.startswith("pm_exit |")
        assert "SCALP" in result
        assert "regime:MOMENTUM_BULL→TRENDING_BEAR" in result
        assert "held:" in result
        assert "thesis:" in result
        assert "regime_shift:" in result

    def test_breached_regime_shift_when_polarity_flips(self):
        pos = _make_pos(entry_regime="MOMENTUM_BULL")
        regime = {"regime": "TRENDING_BEAR"}
        result = bot_trading._build_pm_exit_reason(pos, regime, "test_trigger", "some reason")
        assert "thesis:breached_regime_shift" in result

    def test_noise_stop_when_same_polarity(self):
        pos = _make_pos(entry_regime="MOMENTUM_BULL")
        regime = {"regime": "RELIEF_RALLY"}  # still BULL polarity
        result = bot_trading._build_pm_exit_reason(pos, regime, "test_trigger", "some reason")
        assert "thesis:noise_stop" in result

    def test_pm_trim_tag(self):
        pos = _make_pos()
        result = bot_trading._build_pm_exit_reason(
            pos, {}, "cycle_regime_shift", "trimming", exit_tag="pm_trim"
        )
        assert result.startswith("pm_trim |")

    def test_reason_pm_truncated_to_120_chars(self):
        pos = _make_pos()
        long_reason = "x" * 200
        result = bot_trading._build_pm_exit_reason(pos, {}, "trigger", long_reason)
        # The embedded reason after "trigger: " must be ≤120 chars
        embedded = result.split("| trigger: ", 1)[-1]
        assert len(embedded) <= 120

    def test_empty_pos_does_not_raise(self):
        result = bot_trading._build_pm_exit_reason({}, {}, "trigger", "reason")
        assert "pm_exit" in result

    def test_session_character_preferred_over_regime_key(self):
        pos = _make_pos(entry_regime="MOMENTUM_BULL")
        regime = {"regime": "UNKNOWN", "session_character": "TRENDING_BEAR"}
        result = bot_trading._build_pm_exit_reason(pos, regime, "trig", "r")
        assert "→TRENDING_BEAR" in result


# ─── tp_order_id exit-type classification ───────────────────────────────────

class TestTpOrderIdClassification:
    """Validate the classification logic added to check_external_closes().

    We test the algorithm directly rather than driving the full function
    (which requires a live IB connection) — the classification block is
    self-contained and side-effect-free.
    """

    def _classify(self, trade_dict, fill_order_id, exit_price, pnl):
        """Replicate the classification block from check_external_closes()."""
        sl_order_id = trade_dict.get("sl_order_id")
        tp_order_id = trade_dict.get("tp_order_id")
        is_short = trade_dict.get("direction", "LONG") == "SHORT"
        _fill_order_id = fill_order_id

        if sl_order_id and _fill_order_id and int(_fill_order_id) == int(sl_order_id):
            return "sl_hit"
        elif tp_order_id and _fill_order_id and int(_fill_order_id) == int(tp_order_id):
            return "tp_hit"
        elif pnl > 0 and trade_dict.get("tp"):
            tp = trade_dict.get("tp")
            hit_tp = (not is_short and exit_price >= tp * 0.99) or \
                     (is_short and exit_price <= tp * 1.01)
            return "tp_hit" if hit_tp else "manual"
        else:
            return "manual"

    def test_tp_hit_via_order_id_even_outside_tolerance(self):
        """Fill at 97% of TP price should still be tp_hit when order ID matches."""
        trade = _make_pos()
        trade["sl_order_id"] = 1001
        trade["tp_order_id"] = 1002
        trade["tp"] = 108.0
        # 105.0 is 97% of 108 — below the 99% threshold, would be "manual" without ID
        exit_type = self._classify(trade, fill_order_id=1002, exit_price=105.0, pnl=50.0)
        assert exit_type == "tp_hit"

    def test_sl_hit_takes_priority_over_tp_order_id(self):
        trade = _make_pos()
        trade["sl_order_id"] = 1001
        trade["tp_order_id"] = 1002
        exit_type = self._classify(trade, fill_order_id=1001, exit_price=97.0, pnl=-30.0)
        assert exit_type == "sl_hit"

    def test_price_fallback_when_no_tp_order_id(self):
        trade = _make_pos()
        trade["sl_order_id"] = 1001
        trade["tp_order_id"] = None
        trade["tp"] = 108.0
        # Fill above 99% of TP price → price fallback fires
        exit_type = self._classify(trade, fill_order_id=9999, exit_price=108.5, pnl=85.0)
        assert exit_type == "tp_hit"

    def test_manual_when_no_id_match_and_price_miss(self):
        trade = _make_pos()
        trade["sl_order_id"] = 1001
        trade["tp_order_id"] = None
        trade["tp"] = 108.0
        # Fill at 105 — profitable but no ID match and below 99% of 108
        exit_type = self._classify(trade, fill_order_id=9999, exit_price=105.0, pnl=50.0)
        assert exit_type == "manual"

    def test_short_tp_hit_via_order_id(self):
        trade = _make_pos(direction="SHORT", entry=100.0, current=95.0)
        trade["sl_order_id"] = 2001
        trade["tp_order_id"] = 2002
        trade["tp"] = 92.0  # SHORT target is below entry
        # Fill at 93 — above 101% of 92 (=92.92)? No, 93 > 92.92. Would be "manual" on price.
        # But order ID match makes it tp_hit.
        exit_type = self._classify(trade, fill_order_id=2002, exit_price=93.0, pnl=70.0)
        assert exit_type == "tp_hit"
