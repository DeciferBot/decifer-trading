"""
test_portfolio_manager.py — Tests for portfolio_manager.py

Covers the pre-Opus forced-exit blocks:
  - long_only_symbols SHORT guard (existing)
  - INTRADAY adverse move forced EXIT (new, 2026-04-22)
"""

import os
import sys
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Decifer module
for _mod in [
    "ib_async", "ib_insync", "anthropic", "yfinance",
    "praw", "feedparser", "tvDatafeed", "requests_html",
]:
    sys.modules.setdefault(_mod, MagicMock())

import config as _config_mod

_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-6",
    "max_tokens": 1000,
    "mongo_uri": "",
    "db_name": "test",
    "long_only_symbols": {"SPXS", "SQQQ", "UVXY"},
    "intraday_adverse_exit_pct": 3.0,
    "portfolio_manager": {"enabled": True},
}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG[_k] = _v
else:
    _config_mod.CONFIG = _cfg

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_position(
    symbol="AAPL",
    direction="LONG",
    trade_type="SWING",
    entry=100.0,
    current=100.0,
):
    return {
        "symbol": symbol,
        "direction": direction,
        "trade_type": trade_type,
        "entry": entry,
        "current": current,
        "qty": 100,
        "score": 30,
        "entry_score": 30,
        "open_time": "2026-04-22T09:30:00+00:00",
        "instrument": "stock",
        "regime": "TRENDING_UP",
    }


_REGIME = {"regime": "TRENDING_UP", "vix": 18.0}


# ── INTRADAY adverse move tests ───────────────────────────────────────────────


class TestIntradayAdverseExit:
    """
    The forced-exit block runs before Opus sees the position.
    We test by calling run_portfolio_review() with Opus mocked to never fire.
    Any EXIT in the result that comes back without an Opus call is a forced exit.
    """

    def _run(self, positions, config_override=None):
        """
        Call run_portfolio_review with Opus patched out.
        Returns the list of actions.
        """
        cfg = dict(_cfg)
        if config_override:
            cfg.update(config_override)

        with patch("portfolio_manager.CONFIG", cfg), \
             patch("portfolio_manager.get_earnings_within_hours", return_value=[]), \
             patch("portfolio_manager.anthropic") as mock_anthropic:
            # If Opus is called, return a HOLD for every position
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="ACTION: HOLD\nREASON: thesis intact")]
            mock_client.messages.create.return_value = mock_response

            import portfolio_manager as pm
            return pm.run_portfolio_review(
                open_positions=positions,
                all_scored=[],
                regime=_REGIME,
                news_sentiment={},
                portfolio_value=100_000,
                trigger="drawdown",
            )

    def test_intraday_long_adverse_move_forced_exit(self):
        """INTRADAY LONG down 3.5% → forced EXIT before Opus."""
        pos = _make_position(
            direction="LONG",
            trade_type="INTRADAY",
            entry=100.0,
            current=96.5,  # -3.5%
        )
        results = self._run([pos])
        exits = [r for r in results if r["action"] == "EXIT"]
        assert len(exits) == 1
        assert exits[0]["symbol"] == "AAPL"
        assert "intraday_adverse_move" in exits[0]["reasoning"]

    def test_intraday_short_adverse_move_forced_exit(self):
        """INTRADAY SHORT up 3.5% → forced EXIT before Opus."""
        pos = _make_position(
            direction="SHORT",
            trade_type="INTRADAY",
            entry=100.0,
            current=103.5,  # price rose +3.5% against the short
        )
        results = self._run([pos])
        exits = [r for r in results if r["action"] == "EXIT"]
        assert len(exits) == 1
        assert exits[0]["symbol"] == "AAPL"
        assert "intraday_adverse_move" in exits[0]["reasoning"]

    def test_intraday_long_small_move_not_forced(self):
        """INTRADAY LONG down 2% → below threshold, passes to Opus."""
        pos = _make_position(
            direction="LONG",
            trade_type="INTRADAY",
            entry=100.0,
            current=98.0,  # -2%, below 3% threshold
        )
        results = self._run([pos])
        # No intraday_adverse_move EXIT — position went to Opus
        forced = [r for r in results if "intraday_adverse_move" in r.get("reasoning", "")]
        assert len(forced) == 0

    def test_non_intraday_adverse_move_not_forced(self):
        """SWING down 5% → rule does not apply, only INTRADAY is gated."""
        pos = _make_position(
            direction="LONG",
            trade_type="SWING",
            entry=100.0,
            current=95.0,  # -5%, would trigger if INTRADAY
        )
        results = self._run([pos])
        forced = [r for r in results if "intraday_adverse_move" in r.get("reasoning", "")]
        assert len(forced) == 0

    def test_intraday_adverse_threshold_configurable(self):
        """threshold=2.0 in config → fires at 2.5% adverse move."""
        pos = _make_position(
            direction="LONG",
            trade_type="INTRADAY",
            entry=100.0,
            current=97.5,  # -2.5%, above 2.0 threshold but below default 3.0
        )
        results = self._run([pos], config_override={"intraday_adverse_exit_pct": 2.0})
        exits = [r for r in results if "intraday_adverse_move" in r.get("reasoning", "")]
        assert len(exits) == 1

    def test_intraday_long_exactly_at_threshold_triggers(self):
        """Exactly at 3.0% → triggers (>= not >)."""
        pos = _make_position(
            direction="LONG",
            trade_type="INTRADAY",
            entry=100.0,
            current=97.0,  # exactly -3.0%
        )
        results = self._run([pos])
        exits = [r for r in results if "intraday_adverse_move" in r.get("reasoning", "")]
        assert len(exits) == 1

    def test_intraday_profitable_long_not_forced(self):
        """INTRADAY LONG up 5% — no adverse move, not forced."""
        pos = _make_position(
            direction="LONG",
            trade_type="INTRADAY",
            entry=100.0,
            current=105.0,  # +5%, going the right way
        )
        results = self._run([pos])
        forced = [r for r in results if "intraday_adverse_move" in r.get("reasoning", "")]
        assert len(forced) == 0

    def test_multiple_positions_only_adverse_forced(self):
        """Mix of positions — only the adverse INTRADAY one is forced."""
        pos_adverse = _make_position(
            symbol="GE",
            direction="LONG",
            trade_type="INTRADAY",
            entry=100.0,
            current=96.0,  # -4%
        )
        pos_ok = _make_position(
            symbol="AAPL",
            direction="LONG",
            trade_type="INTRADAY",
            entry=100.0,
            current=99.0,  # -1%, fine
        )
        results = self._run([pos_adverse, pos_ok])
        forced = [r for r in results if "intraday_adverse_move" in r.get("reasoning", "")]
        assert len(forced) == 1
        assert forced[0]["symbol"] == "GE"
