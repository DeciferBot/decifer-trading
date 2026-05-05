"""
test_margin_readiness.py — Unit tests for margin-mode readiness fixes.

Covers:
1. account_values_updated_at is stamped by IBKR account value callback
2. Margin mode blocks with account_values_missing_block when timestamp absent
3. Margin mode blocks with account_values_stale_block when timestamp too old
4. Missing ExcessLiquidity gives excess_liquidity_missing_block
5. Missing AvailableFunds gives available_funds_missing_block
6. Low ExcessLiquidity gives excess_liquidity_buffer_block
7. Low AvailableFunds gives available_funds_buffer_block
8. Equity mode is unchanged — does not require account_values_updated_at
9. Options exposure blocks propagate exp_code into orders_core._block_reason
10. build_margin_snapshot includes required audit fields
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


import bot_state  # noqa: E402
import risk  # noqa: E402
from risk import build_margin_snapshot, check_combined_exposure  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _margin_cfg(enabled: bool = True, **overrides) -> dict:
    base = {
        "enabled": enabled,
        "equity_gross_cap": 1.0,
        "margin_gross_cap": 1.5,
        "excess_liquidity_buffer": 0.10,
        "available_funds_buffer": 0.05,
        "max_account_values_age_seconds": 300,
    }
    base.update(overrides)
    return base


def _av_full(net_liq=100_000, el=20_000, af=15_000, bp=50_000) -> dict:
    """A complete account_values dict."""
    return {
        "NetLiquidation": net_liq,
        "ExcessLiquidity": el,
        "AvailableFunds": af,
        "BuyingPower": bp,
    }


def _pos(qty=10, price=100.0) -> dict:
    return {"symbol": "MSFT", "qty": qty, "current": price, "entry": price, "instrument": "stock"}


# ─────────────────────────────────────────────────────────────────────────────
# 1. account_values_updated_at is stamped by _on_account_value
# ─────────────────────────────────────────────────────────────────────────────

def test_account_values_updated_at_stamped_by_callback():
    """_on_account_value must write a float timestamp on every accepted tag."""
    from bot_ibkr import _on_account_value

    before = time.time()

    av = MagicMock()
    av.tag = "NetLiquidation"
    av.value = "999000"

    with patch.object(bot_state, "_account_ready", True), \
         patch.object(bot_state, "account_values", {}), \
         patch.dict("bot_state.dash", {"portfolio_value": 0.0}):
        _on_account_value(av)
        ts = bot_state.account_values_updated_at

    assert ts is not None, "account_values_updated_at must be set after callback"
    assert ts >= before, "timestamp must be >= call time"
    assert ts <= time.time(), "timestamp must not be in the future"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Margin mode blocks with account_values_missing_block when no timestamp
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_no_timestamp_gives_missing_block():
    cfg = {"margin_exposure": _margin_cfg(enabled=True), "max_single_position": 0.10}
    with patch.dict("risk.CONFIG", cfg), \
         patch("bot_state.account_values_updated_at", None), \
         patch("bot_state.account_values", _av_full()):
        ok, reason, code = check_combined_exposure("AAPL", 5_000, [], 100_000)
    assert ok is False
    assert code == "account_values_missing_block"
    assert "not yet received" in reason.lower() or "ibkr" in reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Margin mode blocks with account_values_stale_block when too old
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_stale_timestamp_gives_stale_block():
    cfg = {"margin_exposure": _margin_cfg(enabled=True, max_account_values_age_seconds=300),
           "max_single_position": 0.10}
    stale_ts = time.time() - 400  # 400s ago > 300s max
    with patch.dict("risk.CONFIG", cfg), \
         patch("bot_state.account_values_updated_at", stale_ts), \
         patch("bot_state.account_values", _av_full()):
        ok, reason, code = check_combined_exposure("AAPL", 5_000, [], 100_000)
    assert ok is False
    assert code == "account_values_stale_block"
    assert "stale" in reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Missing ExcessLiquidity key gives excess_liquidity_missing_block
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_missing_excess_liquidity_key():
    cfg = {"margin_exposure": _margin_cfg(enabled=True), "max_single_position": 0.10}
    av = {"NetLiquidation": 100_000, "AvailableFunds": 15_000, "BuyingPower": 50_000}
    # ExcessLiquidity intentionally absent
    with patch.dict("risk.CONFIG", cfg), \
         patch("bot_state.account_values_updated_at", time.time()), \
         patch("bot_state.account_values", av):
        ok, reason, code = check_combined_exposure("AAPL", 5_000, [], 100_000)
    assert ok is False
    assert code == "excess_liquidity_missing_block"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Missing AvailableFunds key gives available_funds_missing_block
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_missing_available_funds_key():
    cfg = {"margin_exposure": _margin_cfg(enabled=True), "max_single_position": 0.10}
    # ExcessLiquidity present and healthy; AvailableFunds absent
    av = {"NetLiquidation": 100_000, "ExcessLiquidity": 20_000, "BuyingPower": 50_000}
    with patch.dict("risk.CONFIG", cfg), \
         patch("bot_state.account_values_updated_at", time.time()), \
         patch("bot_state.account_values", av):
        ok, reason, code = check_combined_exposure("AAPL", 5_000, [], 100_000)
    assert ok is False
    assert code == "available_funds_missing_block"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Low ExcessLiquidity gives excess_liquidity_buffer_block
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_low_excess_liquidity_buffer_block():
    cfg = {"margin_exposure": _margin_cfg(enabled=True, excess_liquidity_buffer=0.10),
           "max_single_position": 0.10}
    # el_min = 10% × 100 000 = 10 000; ExcessLiquidity=8 000 → block
    av = _av_full(net_liq=100_000, el=8_000, af=15_000)
    with patch.dict("risk.CONFIG", cfg), \
         patch("bot_state.account_values_updated_at", time.time()), \
         patch("bot_state.account_values", av):
        ok, reason, code = check_combined_exposure("AAPL", 5_000, [_pos()], 100_000)
    assert ok is False
    assert code == "excess_liquidity_buffer_block"
    assert "ExcessLiquidity" in reason


# ─────────────────────────────────────────────────────────────────────────────
# 7. Low AvailableFunds gives available_funds_buffer_block
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_mode_low_available_funds_buffer_block():
    cfg = {"margin_exposure": _margin_cfg(enabled=True, excess_liquidity_buffer=0.05,
                                          available_funds_buffer=0.10),
           "max_single_position": 0.10}
    # EL healthy (5% buf, 15k present > 5k min); AF=8k < 10% × 100k=10k → block
    av = _av_full(net_liq=100_000, el=15_000, af=8_000)
    with patch.dict("risk.CONFIG", cfg), \
         patch("bot_state.account_values_updated_at", time.time()), \
         patch("bot_state.account_values", av):
        ok, reason, code = check_combined_exposure("AAPL", 5_000, [_pos()], 100_000)
    assert ok is False
    assert code == "available_funds_buffer_block"
    assert "AvailableFunds" in reason


# ─────────────────────────────────────────────────────────────────────────────
# 8. Equity mode is unchanged — no freshness check, no account_values needed
# ─────────────────────────────────────────────────────────────────────────────

def test_equity_mode_does_not_require_account_values_updated_at():
    """Equity mode (enabled=False) must pass even when account_values is empty
    and account_values_updated_at is None."""
    cfg = {"margin_exposure": _margin_cfg(enabled=False, equity_gross_cap=1.0),
           "max_single_position": 0.10}
    with patch.dict("risk.CONFIG", cfg), \
         patch("bot_state.account_values_updated_at", None), \
         patch("bot_state.account_values", {}):
        ok, reason, code = check_combined_exposure("AAPL", 5_000, [], 100_000)
    assert ok is True
    assert code == ""


# ─────────────────────────────────────────────────────────────────────────────
# 9. Options exposure blocks propagate exp_code into orders_core._block_reason
# ─────────────────────────────────────────────────────────────────────────────

def test_options_exposure_block_propagates_to_block_reason():
    """execute_buy_option must write exp_code into orders_core._block_reason
    so signal_dispatcher sees a named blocker_flags entry.

    We mock check_combined_exposure to return a known block so the test is
    isolated to the propagation path (not contract construction internals).
    """
    import orders_core
    import sys as _sys

    real_oc = _sys.modules.get("orders_core")
    real_oc._block_reason.pop("AAPL", None)
    real_oc._exposure_block_details.pop("AAPL", None)

    from orders_options import execute_buy_option
    from orders_state import active_trades, _trades_lock

    with _trades_lock:
        active_trades.clear()

    contract_info = {
        "symbol": "AAPL",
        "right": "C",
        "strike": 200.0,
        "expiry_str": "2026-12-19",
        "contracts": 1,
        "mid": 5.0,
    }

    with patch("orders_options.check_combined_exposure",
               return_value=(False, "Portfolio allocation limit: blocked", "equity_gross_cap_block")), \
         patch("orders_options.is_options_market_open", return_value=True), \
         patch.dict("risk.CONFIG", {"max_positions": 100}):
        result = execute_buy_option(
            ib=MagicMock(),
            contract_info=contract_info,
            portfolio_value=100_000,
        )

    assert result is False, "execute_buy_option must return False for exposure block"
    assert "AAPL" in real_oc._block_reason, (
        "_block_reason must be populated so signal_dispatcher emits a named blocker_flag"
    )
    code = real_oc._block_reason.pop("AAPL")
    detail = real_oc._exposure_block_details.pop("AAPL", None)
    assert code == "equity_gross_cap_block", f"Expected equity_gross_cap_block, got {code!r}"
    assert detail is not None, "_exposure_block_details must be populated for options exposure block"
    assert detail.get("instrument") == "option"
    assert detail.get("exp_code") == "equity_gross_cap_block"


# ─────────────────────────────────────────────────────────────────────────────
# 10. build_margin_snapshot includes required audit fields
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_snapshot_contains_required_fields():
    now_ts = time.time()
    av = _av_full(net_liq=1_000_000, el=80_000, af=50_000, bp=200_000)
    cfg = {"margin_exposure": _margin_cfg(enabled=True)}

    with patch("bot_state.account_values", av), \
         patch("bot_state.account_values_updated_at", now_ts), \
         patch.dict("risk.CONFIG", cfg):
        snap = build_margin_snapshot()

    required = {
        "ts", "account_values_updated_at", "account_values_age_seconds",
        "NetLiquidation", "ExcessLiquidity", "AvailableFunds", "BuyingPower",
        "margin_exposure_enabled", "margin_gross_cap",
        "excess_liquidity_buffer", "available_funds_buffer",
    }
    missing = required - snap.keys()
    assert not missing, f"Snapshot missing fields: {missing}"

    assert snap["NetLiquidation"] == 1_000_000
    assert snap["ExcessLiquidity"] == 80_000
    assert snap["AvailableFunds"] == 50_000
    assert snap["BuyingPower"] == 200_000
    assert snap["account_values_age_seconds"] is not None
    assert snap["account_values_age_seconds"] >= 0
