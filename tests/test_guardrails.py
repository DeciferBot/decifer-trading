"""
test_guardrails.py — Unit tests for guardrails._default_trade_type()

Covers the Tier D PRU Quality Compounder fix (Fix B):
- Tier D + Quality Compounder → POSITION anchor
- Tier D + Speculative Theme  → not forced to POSITION
- Non-Tier-D + Quality Compounder → not forced to POSITION
- Missing scanner_tier → no crash, valid string returned
- allowed_trade_types unchanged by default_tt branch (filter_candidates smoke)
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ── bootstrap ──────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser",
             "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

import config as _config_mod  # noqa: E402

_cfg = {
    "log_file": "/dev/null",
    "trade_log": "/dev/null",
    "order_log": "/dev/null",
    "anthropic_api_key": "test-key",
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1000,
}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg


from guardrails import _default_trade_type  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sig(scanner_tier=None, primary_archetype=None, trade_context=None, **extra):
    """Build a minimal signal dict for _default_trade_type tests."""
    s: dict = {
        "symbol": "TEST",
        "direction": "long",
        "score": 30,
        "regime": {"regime": "TRENDING_UP"},
    }
    if scanner_tier is not None:
        s["scanner_tier"] = scanner_tier
    if primary_archetype is not None:
        s["primary_archetype"] = primary_archetype
    if trade_context is not None:
        s["trade_context"] = trade_context
    s.update(extra)
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Fix B tests — _default_trade_type Tier D PRU routing
# ─────────────────────────────────────────────────────────────────────────────

class TestDefaultTradeTypeTierD:

    def test_tier_d_quality_compounder_returns_position(self):
        """Tier D + Quality Compounder must return 'POSITION' as default anchor."""
        result = _default_trade_type(_sig(
            scanner_tier="D",
            primary_archetype="Quality Compounder",
        ))
        assert result == "POSITION"

    def test_tier_d_speculative_theme_not_forced_to_position(self):
        """Tier D + Speculative Theme must NOT be forced to POSITION.
        Falls through to the classify_trade_type() path (returns INTRADAY by default
        when no trade_context is attached)."""
        result = _default_trade_type(_sig(
            scanner_tier="D",
            primary_archetype="Speculative Theme",
        ))
        assert result != "POSITION"
        assert result in ("INTRADAY", "SWING", "POSITION")  # valid string

    def test_tier_d_growth_leader_not_forced_to_position(self):
        """Tier D + Growth Leader: not in the confirmed fundamental archetype list,
        must not be forced to POSITION."""
        result = _default_trade_type(_sig(
            scanner_tier="D",
            primary_archetype="Growth Leader",
        ))
        assert result != "POSITION"

    def test_non_tier_d_quality_compounder_not_forced_to_position(self):
        """A non-Tier-D candidate with archetype Quality Compounder must not be
        forced to POSITION — the guard is Tier D-specific."""
        for tier in ("A", "B", "C", None):
            sig = _sig(primary_archetype="Quality Compounder")
            if tier is not None:
                sig["scanner_tier"] = tier
            result = _default_trade_type(sig)
            assert result != "POSITION", (
                f"scanner_tier={tier!r} Quality Compounder should not return POSITION"
            )

    def test_missing_scanner_tier_does_not_raise(self):
        """Signal dict with no scanner_tier key must not raise — falls through
        to existing classify_trade_type logic."""
        result = _default_trade_type(_sig())
        assert isinstance(result, str)
        assert result in ("INTRADAY", "SWING", "POSITION")

    def test_missing_primary_archetype_does_not_raise(self):
        """Tier D signal with no primary_archetype key must not raise — guard
        reads None which does not match 'Quality Compounder'."""
        result = _default_trade_type(_sig(scanner_tier="D"))
        # No archetype → falls through to classify_trade_type
        assert isinstance(result, str)
        assert result in ("INTRADAY", "SWING", "POSITION")

    def test_tier_d_quality_compounder_ignores_trade_context(self):
        """Even when a trade_context is present, Tier D + Quality Compounder
        must return POSITION before reaching classify_trade_type."""
        tc = {"earnings_days_away": 3, "time_of_day_window": "CLOSE"}
        result = _default_trade_type(_sig(
            scanner_tier="D",
            primary_archetype="Quality Compounder",
            trade_context=tc,
        ))
        assert result == "POSITION"


# ─────────────────────────────────────────────────────────────────────────────
# Smoke: allowed_trade_types unchanged (filter_candidates does not strip types)
# ─────────────────────────────────────────────────────────────────────────────

class TestAllowedTradeTypesUnchanged:

    def test_tier_d_quality_compounder_allowed_includes_intraday_and_swing(self):
        """filter_candidates must not strip INTRADAY or SWING from allowed list
        for a Tier D Quality Compounder — only default_tt changes."""
        from guardrails import filter_candidates

        sig = _sig(
            scanner_tier="D",
            primary_archetype="Quality Compounder",
            price=100.0,
        )
        # Patch the lazy imports inside filter_candidates so no real state is read
        with (
            patch("orders_guards.has_open_order_for", return_value=False),
            patch("orders_state._is_recently_closed", return_value=False),
            patch("orders_state.is_failed_thesis_blocked", return_value=(False, "")),
        ):
            result = filter_candidates(
                [sig],
                open_symbols=set(),
                regime={"regime": "TRENDING_UP"},
            )

        # Candidate must survive all gates and carry the correct trade type fields
        assert result, "Tier D Quality Compounder must not be dropped by filter_candidates"
        candidate = result[0]
        allowed = candidate.get("allowed_trade_types", [])
        assert "INTRADAY" in allowed, "INTRADAY must remain in allowed_trade_types"
        assert "SWING" in allowed, "SWING must remain in allowed_trade_types"
        assert "POSITION" in allowed, "POSITION must remain in allowed_trade_types"
        assert candidate.get("default_trade_type") == "POSITION"
