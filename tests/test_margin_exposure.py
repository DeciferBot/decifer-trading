"""
test_margin_exposure.py — Unit tests for check_combined_exposure() margin-aware logic.

Covers the margin-aware exposure framework (intentional risk-policy change):
- Equity mode (default): equity_gross_cap block fires correctly
- Equity mode fallback to max_portfolio_allocation when margin_exposure absent
- Margin mode: margin_gross_cap, excess_liquidity_buffer, available_funds_buffer
- Cross-instrument block still returns cross_instrument_block code
- Zero portfolio_value never raises (guard)
- 3-tuple return invariant: every path returns (bool, str, str)
"""

from __future__ import annotations

import os
import sys
import time
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


from risk import check_combined_exposure  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pos(symbol: str, qty: int, price: float, instrument: str = "stock") -> dict:
    return {"symbol": symbol, "qty": qty, "current": price, "entry": price,
            "instrument": instrument}


def _set_margin_cfg(enabled: bool, **overrides):
    """Patch CONFIG with a margin_exposure block."""
    base = {
        "enabled": enabled,
        "equity_gross_cap": 1.0,
        "margin_gross_cap": 1.5,
        "excess_liquidity_buffer": 0.10,
        "available_funds_buffer": 0.05,
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# 1. Portfolio_value=0 guard — must never divide by zero
# ─────────────────────────────────────────────────────────────────────────────

def test_zero_portfolio_value_returns_ok():
    ok, reason, code = check_combined_exposure("AAPL", 10_000, [], portfolio_value=0)
    assert ok is True
    assert code == ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. Equity mode (enabled=False) — under cap → OK
# ─────────────────────────────────────────────────────────────────────────────

def test_equity_mode_under_cap_ok():
    cfg_patch = {"margin_exposure": _set_margin_cfg(enabled=False, equity_gross_cap=1.0),
                 "max_portfolio_allocation": 1.0, "max_single_position": 0.10}
    with patch.dict("risk.CONFIG", cfg_patch):
        positions = [_pos("MSFT", 10, 100.0)]  # $1 000 deployed
        ok, reason, code = check_combined_exposure(
            "AAPL", 5_000, positions, portfolio_value=100_000
        )
    assert ok is True
    assert code == ""


# ─────────────────────────────────────────────────────────────────────────────
# 3. Equity mode — over equity_gross_cap → equity_gross_cap_block
# ─────────────────────────────────────────────────────────────────────────────

def test_equity_mode_over_cap_blocked():
    cfg_patch = {"margin_exposure": _set_margin_cfg(enabled=False, equity_gross_cap=1.0),
                 "max_portfolio_allocation": 1.0, "max_single_position": 0.10}
    with patch.dict("risk.CONFIG", cfg_patch):
        # 95 000 already deployed; adding 10 000 → 105% > 100%
        positions = [_pos("MSFT", 100, 950.0)]  # $95 000
        ok, reason, code = check_combined_exposure(
            "AAPL", 10_000, positions, portfolio_value=100_000
        )
    assert ok is False
    assert code == "equity_gross_cap_block"
    assert "allocation" in reason.lower() or "cap" in reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Equity mode fallback — margin_exposure key absent → uses max_portfolio_allocation
# ─────────────────────────────────────────────────────────────────────────────

def test_equity_mode_fallback_to_max_portfolio_allocation():
    """When margin_exposure key is absent, equity_gross_cap defaults to max_portfolio_allocation."""
    cfg_patch = {"max_portfolio_allocation": 0.80, "max_single_position": 0.10}
    # Remove margin_exposure entirely so the fallback path is exercised
    import risk as _risk
    original = _risk.CONFIG.pop("margin_exposure", None)
    try:
        with patch.dict("risk.CONFIG", cfg_patch, clear=False):
            positions = [_pos("MSFT", 100, 750.0)]  # $75 000 deployed
            ok, reason, code = check_combined_exposure(
                "AAPL", 10_000, positions, portfolio_value=100_000
            )
        # 85% > 80% → blocked
        assert ok is False
        assert code == "equity_gross_cap_block"
    finally:
        if original is not None:
            _risk.CONFIG["margin_exposure"] = original


# ─────────────────────────────────────────────────────────────────────────────
# 5. Margin mode — over margin_gross_cap → margin_gross_cap_block
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_over_gross_cap_blocked():
    cfg_patch = {"margin_exposure": _set_margin_cfg(enabled=True, margin_gross_cap=1.5),
                 "max_single_position": 0.10}
    fake_av = {"NetLiquidation": "100000", "ExcessLiquidity": "20000", "AvailableFunds": "15000"}
    with patch.dict("risk.CONFIG", cfg_patch), \
         patch("bot_state.account_values_updated_at", time.time()), \
         patch("bot_state.account_values", fake_av):
        positions = [_pos("MSFT", 100, 1_400.0)]  # $140 000 deployed
        ok, reason, code = check_combined_exposure(
            "AAPL", 15_000, positions, portfolio_value=100_000
        )
    # 155 000 / 100 000 = 155% > 150% → blocked
    assert ok is False
    assert code == "margin_gross_cap_block"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Margin mode — excess_liquidity below buffer → excess_liquidity_buffer_block
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_excess_liquidity_buffer_blocked():
    cfg_patch = {"margin_exposure": _set_margin_cfg(enabled=True, excess_liquidity_buffer=0.10),
                 "max_single_position": 0.10}
    # net_liq=$100 000, el_min = 10% × 100 000 = $10 000; ExcessLiquidity=$8 000 → block
    fake_av = {"NetLiquidation": "100000", "ExcessLiquidity": "8000", "AvailableFunds": "15000"}
    with patch.dict("risk.CONFIG", cfg_patch), \
         patch("bot_state.account_values_updated_at", time.time()), \
         patch("bot_state.account_values", fake_av):
        positions = [_pos("MSFT", 10, 100.0)]  # minimal deployment, won't hit gross cap
        ok, reason, code = check_combined_exposure(
            "AAPL", 5_000, positions, portfolio_value=100_000
        )
    assert ok is False
    assert code == "excess_liquidity_buffer_block"
    assert "ExcessLiquidity" in reason


# ─────────────────────────────────────────────────────────────────────────────
# 7. Margin mode — available_funds below buffer → available_funds_buffer_block
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_available_funds_buffer_blocked():
    cfg_patch = {"margin_exposure": _set_margin_cfg(
        enabled=True, excess_liquidity_buffer=0.05, available_funds_buffer=0.10),
                 "max_single_position": 0.10}
    # ExcessLiquidity=$15 000 (ok for 5% buffer on $100 000)
    # AvailableFunds=$8 000 < 10% × $100 000 = $10 000 → block
    fake_av = {"NetLiquidation": "100000", "ExcessLiquidity": "15000", "AvailableFunds": "8000"}
    with patch.dict("risk.CONFIG", cfg_patch), \
         patch("bot_state.account_values_updated_at", time.time()), \
         patch("bot_state.account_values", fake_av):
        positions = [_pos("MSFT", 10, 100.0)]
        ok, reason, code = check_combined_exposure(
            "AAPL", 5_000, positions, portfolio_value=100_000
        )
    assert ok is False
    assert code == "available_funds_buffer_block"
    assert "AvailableFunds" in reason


# ─────────────────────────────────────────────────────────────────────────────
# 8. Margin mode — all buffers healthy → OK
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_all_buffers_healthy_ok():
    cfg_patch = {"margin_exposure": _set_margin_cfg(enabled=True),
                 "max_single_position": 0.10}
    # 60 000 deployed + 5 000 new = 65% < 150%; EL=$25 000 > 10%; AF=$20 000 > 5%
    fake_av = {"NetLiquidation": "100000", "ExcessLiquidity": "25000", "AvailableFunds": "20000"}
    with patch.dict("risk.CONFIG", cfg_patch), \
         patch("bot_state.account_values_updated_at", time.time()), \
         patch("bot_state.account_values", fake_av):
        positions = [_pos("MSFT", 100, 600.0)]  # $60 000
        ok, reason, code = check_combined_exposure(
            "AAPL", 5_000, positions, portfolio_value=100_000
        )
    assert ok is True
    assert code == ""


# ─────────────────────────────────────────────────────────────────────────────
# 9. Cross-instrument block returns cross_instrument_block (unchanged behaviour)
# ─────────────────────────────────────────────────────────────────────────────

def test_cross_instrument_block_code():
    cfg_patch = {"max_single_position": 0.10, "margin_exposure": _set_margin_cfg(enabled=False),
                 "max_portfolio_allocation": 1.0}
    with patch.dict("risk.CONFIG", cfg_patch):
        # Existing stock exposure; adding option would cross-instrument and exceed 10%
        existing_stock = {"symbol": "AAPL", "qty": 100, "current": 150.0,
                          "entry": 150.0, "instrument": "stock"}
        # $15 000 stock already; adding $5 000 option = $20 000 / $100 000 = 20% > 10%
        ok, reason, code = check_combined_exposure(
            "AAPL", 5_000, [existing_stock], portfolio_value=100_000, instrument="option"
        )
    assert ok is False
    assert code == "cross_instrument_block"
