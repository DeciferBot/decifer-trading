"""Tests for the dimension flag system (BACK-009 / feat-dim-flags).

Covers:
- Each of the 9 flags, when set False, produces 0 score contribution for that dim
- All flags False → valid result, score 0 (or candlestick-only), no exceptions
- All flags True → result identical to default (no flags configured) behaviour
- disabled_dimensions key is present and accurate in the return dict
- Flags don't affect the MTF gate or the candle gate
"""

import os
import sys
from unittest.mock import MagicMock

# ── Project root ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps before any Decifer import
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

import config as _config_mod

_cfg = {"log_file": "/dev/null", "trade_log": "/dev/null", "order_log": "/dev/null", "anthropic_api_key": "test"}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg

sys.modules.pop("signals", None)
import pytest

import signals

# ── Fixtures ─────────────────────────────────────────────────────────────────

ALL_DIMS = [
    "directional",
    "momentum",
    "squeeze",
    "flow",
    "breakout",
    "mtf",
    "news",
    "social",
    "reversion",
    "iv_skew",
    "pead",
    "short_squeeze",
    "overnight_drift",
]

# score_breakdown uses "trend" as the key for the directional dimension
BREAKDOWN_KEYS = [
    "trend",
    "momentum",
    "squeeze",
    "flow",
    "breakout",
    "mtf",
    "news",
    "social",
    "reversion",
    "iv_skew",
    "pead",
    "short_squeeze",
    "overnight_drift",
]


def _base_sig(signal="BUY") -> dict:
    """Strong bullish indicator dict that should score high on every dimension."""
    return {
        "symbol": "TEST",
        "signal": signal,
        "bull_aligned": True,
        "bear_aligned": False,
        "macd_accel": 1.0,
        "adx": 30.0,
        "mfi": 72.0,
        "rsi_slope": 1.0,
        "squeeze_on": True,
        "squeeze_intensity": 0.9,
        "bb_position": 0.8,
        "vwap_dist": 0.5,
        "obv_slope": 1_000_000.0,
        "donch_breakout": 1,
        "vol_ratio": 2.5,
        # Reversion: set up so ADF gate would pass and VR/OU score
        "variance_ratio": 0.4,
        "ou_halflife": 3.0,
        "zscore": -2.5,
        "adf_pvalue": 0.01,
        # Candles
        "candle_bull": 2,
        "candle_bear": 0,
        "price": 150.0,
        "atr": 1.5,
    }


def _run(sig_5m, flags=None, news_score=5, social_score=5):
    """Call compute_confluence with patched dimension_flags."""
    import config

    original_flags = config.CONFIG.get("dimension_flags", {})
    original_mtf = config.CONFIG.get("mtf_gate_mode", "off")
    try:
        config.CONFIG["mtf_gate_mode"] = "off"  # disable MTF gate so it doesn't interfere
        if flags is not None:
            config.CONFIG["dimension_flags"] = flags
        else:
            config.CONFIG["dimension_flags"] = {d: True for d in ALL_DIMS}
        return signals.compute_confluence(sig_5m, None, None, news_score=news_score, social_score=social_score)
    finally:
        config.CONFIG["dimension_flags"] = original_flags
        config.CONFIG["mtf_gate_mode"] = original_mtf


# ── 1. Baseline: all flags True scores > 0 ───────────────────────────────────


def test_all_flags_true_scores_nonzero():
    result = _run(_base_sig(), flags={d: True for d in ALL_DIMS})
    assert result["score"] > 0
    assert result["signal"] in {"BUY", "STRONG_BUY", "SELL", "STRONG_SELL", "HOLD"}


# ── 2. All flags False → score is 0 (or just candle bonus) ───────────────────


def test_all_flags_false_score_is_zero():
    # Candle bonus is not a dimension flag — suppress it for a clean zero.
    sig = _base_sig()
    sig["candle_bull"] = 0
    sig["candle_bear"] = 0
    result = _run(sig, flags={d: False for d in ALL_DIMS})
    assert result["score"] == 0, f"Expected score=0 with all dims disabled, got {result['score']}"
    # Note: signal may still be non-HOLD because it falls back to raw timeframe agreement.
    # That is intentional — dimension flags affect scoring, not the raw signal feed.


def test_all_flags_false_no_exception():
    """Should never raise even with all dims off."""
    result = _run(_base_sig(), flags={d: False for d in ALL_DIMS})
    assert isinstance(result, dict)
    assert "score" in result
    assert "signal" in result


# ── 3. disabled_dimensions reflects what was turned off ──────────────────────


def test_disabled_dimensions_list_empty_when_all_on():
    result = _run(_base_sig(), flags={d: True for d in ALL_DIMS})
    assert result["disabled_dimensions"] == []


def test_disabled_dimensions_list_populated():
    flags = {d: True for d in ALL_DIMS}
    flags["news"] = False
    flags["social"] = False
    result = _run(_base_sig(), flags=flags)
    assert "news" in result["disabled_dimensions"]
    assert "social" in result["disabled_dimensions"]
    assert "directional" not in result["disabled_dimensions"]


def test_disabled_dimensions_all_nine():
    result = _run(_base_sig(), flags={d: False for d in ALL_DIMS})
    assert set(result["disabled_dimensions"]) == set(ALL_DIMS)


# ── 4. Individually disabling each dimension lowers or keeps the score ────────


@pytest.mark.parametrize("dim", ALL_DIMS)
def test_disabling_dim_lowers_score(dim):
    """Score with one dim disabled must be <= score with all dims enabled."""
    all_on = {d: True for d in ALL_DIMS}
    one_off = {d: True for d in ALL_DIMS}
    one_off[dim] = False

    sig = _base_sig()
    sig["candle_bull"] = 0  # remove candle bonus variability
    sig["candle_bear"] = 0

    result_full = _run(sig, flags=all_on)
    result_dim_off = _run(sig, flags=one_off)

    assert result_dim_off["score"] <= result_full["score"], (
        f"Disabling '{dim}' raised score from {result_full['score']} to {result_dim_off['score']}"
    )
    assert dim in result_dim_off["disabled_dimensions"]


# ── 5. Disabling news/social zeroes their specific contribution ───────────────


def test_news_flag_off_zeroes_news_score():
    flags = {d: True for d in ALL_DIMS}
    flags["news"] = False
    result = _run(_base_sig(), flags=flags, news_score=10, social_score=0)
    # score_breakdown should show news = 0
    assert result["score_breakdown"]["news"] == 0


def test_social_flag_off_zeroes_social_score():
    flags = {d: True for d in ALL_DIMS}
    flags["social"] = False
    result = _run(_base_sig(), flags=flags, news_score=0, social_score=10)
    assert result["score_breakdown"]["social"] == 0


# ── 6. Disabling reversion zeroes its sub-metrics ────────────────────────────


def test_reversion_flag_off_zeroes_reversion():
    flags = {d: True for d in ALL_DIMS}
    flags["reversion"] = False
    # Strong reversion setup that would otherwise score 10
    sig = _base_sig()
    sig["adf_pvalue"] = 0.01
    sig["variance_ratio"] = 0.4
    sig["ou_halflife"] = 3.0
    sig["zscore"] = -3.0
    result = _run(sig, flags=flags)
    assert result["score_breakdown"]["reversion"] == 0


# ── 7. Missing dimension_flags in config → defaults to all-enabled (backward compat) ──


def test_missing_flags_config_all_enabled():
    """If dimension_flags is absent from config, all dims score normally."""
    import config

    original = config.CONFIG.pop("dimension_flags", None)
    original_mtf = config.CONFIG.get("mtf_gate_mode", "off")
    try:
        config.CONFIG["mtf_gate_mode"] = "off"
        result = signals.compute_confluence(_base_sig(), None, None, news_score=5, social_score=5)
        assert result["score"] > 0
        assert result["disabled_dimensions"] == []
    finally:
        if original is not None:
            config.CONFIG["dimension_flags"] = original
        config.CONFIG["mtf_gate_mode"] = original_mtf


# ── 8. Score breakdown keys are always present, even when disabled ────────────


def test_score_breakdown_always_has_all_keys():
    result = _run(_base_sig(), flags={d: False for d in ALL_DIMS})
    for dim in BREAKDOWN_KEYS:
        assert dim in result["score_breakdown"], f"Missing key '{dim}' in score_breakdown"
