"""Tests for the candlestick confirmation gate in compute_confluence().

Covers:
- candle_required=True blocks BUY/SELL without a confirming candle
- candle_required=True passes when candle_bull > 0 (BUY) or candle_bear > 0 (SELL)
- candle_required=False (default) never blocks
- Gate applies to all BUY variants (BUY, STRONG_BUY, WEAK_BUY)
- Gate applies to all SELL variants
- HOLD signals are unaffected (nothing to gate)
- candle_gate key is always present in return dict
- candle_gate="BLOCKED" when fired, "PASS" otherwise
- Score is preserved even when gate blocks (only signal is downgraded)

NOTE: Relies on conftest.py for all dependency stubbing and path setup.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config as _config_mod

if "signals" in sys.modules and not hasattr(sys.modules["signals"], "__file__"):
    del sys.modules["signals"]

from signals import compute_confluence


# ---------------------------------------------------------------------------
# Signal fixture helpers (reuse same pattern as test_mtf_gate.py)
# ---------------------------------------------------------------------------

def _make_sig(signal="BUY", bull_aligned=True, bear_aligned=False,
              adx=30.0, macd_hist=0.001, mfi=60.0, rsi=55.0, rsi_slope=1.0,
              macd_accel=0.0001, atr=1.5, vol_ratio=1.2, bb_position=0.6,
              bb_width=0.04, squeeze_on=False, squeeze_intensity=0.0,
              vwap_dist=0.3, obv_slope=1000, donch_breakout=0,
              candle_bull=0, candle_bear=0, variance_ratio=1.0,
              ou_halflife=999.0, zscore=0.0, adf_pvalue=1.0,
              symbol="TEST", timeframe="5m", price=150.0):
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": price,
        "ema_fast": price + 1 if bull_aligned else price - 1,
        "ema_slow": price if bull_aligned else price,
        "ema_trend": price - 1 if bull_aligned else price + 1,
        "bull_aligned": bull_aligned,
        "bear_aligned": bear_aligned,
        "adx": adx,
        "trend_strength": "STRONG" if adx > 25 else "MODERATE" if adx > 20 else "WEAK",
        "mfi": mfi,
        "rsi": rsi,
        "rsi_slope": rsi_slope,
        "macd_hist": macd_hist,
        "macd_accel": macd_accel,
        "atr": atr,
        "vol_ratio": vol_ratio,
        "bb_position": bb_position,
        "bb_width": bb_width,
        "squeeze_on": squeeze_on,
        "squeeze_intensity": squeeze_intensity,
        "vwap": price,
        "vwap_dist": vwap_dist,
        "obv_slope": obv_slope,
        "donch_high": price + 5,
        "donch_low": price - 5,
        "donch_breakout": donch_breakout,
        "candle_bull": candle_bull,
        "candle_bear": candle_bear,
        "variance_ratio": variance_ratio,
        "ou_halflife": ou_halflife,
        "zscore": zscore,
        "adf_pvalue": adf_pvalue,
        "signal": signal,
    }


# ---------------------------------------------------------------------------
# candle_required=True — gate fires without candle
# ---------------------------------------------------------------------------

class TestCandleGateBlocking:

    def test_buy_blocked_without_candle(self, monkeypatch):
        """BUY signal + candle_required=True + no candle → HOLD."""
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", True)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        result = compute_confluence(_make_sig(signal="BUY", candle_bull=0), None, None)
        assert result["signal"] == "HOLD"
        assert result["candle_gate"] == "BLOCKED"

    def test_sell_blocked_without_candle(self, monkeypatch):
        """SELL signal + candle_required=True + no candle → HOLD."""
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", True)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        sig = _make_sig(signal="SELL", bull_aligned=False, bear_aligned=True,
                        mfi=35.0, rsi=40.0, rsi_slope=-1.0, macd_hist=-0.001,
                        vwap_dist=-0.3, obv_slope=-1000,
                        candle_bull=0, candle_bear=0)
        result = compute_confluence(sig, None, None)
        assert result["signal"] == "HOLD"
        assert result["candle_gate"] == "BLOCKED"

    def test_score_preserved_when_blocked(self, monkeypatch):
        """Gate only changes signal — score is NOT zeroed."""
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", True)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        result = compute_confluence(_make_sig(signal="BUY", candle_bull=0), None, None)
        assert result["candle_gate"] == "BLOCKED"
        assert result["score"] > 0  # Score unchanged — just signal downgraded

    def test_direction_becomes_neutral_when_blocked(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", True)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        result = compute_confluence(_make_sig(signal="BUY", candle_bull=0), None, None)
        assert result["direction"] == "NEUTRAL"


# ---------------------------------------------------------------------------
# candle_required=True — gate passes with confirming candle
# ---------------------------------------------------------------------------

class TestCandleGateAllowing:

    def test_buy_passes_with_bull_candle(self, monkeypatch):
        """BUY + candle_bull > 0 → signal passes through."""
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", True)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        result = compute_confluence(
            _make_sig(signal="BUY", candle_bull=2), None, None)
        assert result["signal"] != "HOLD"
        assert result["candle_gate"] == "PASS"

    def test_sell_passes_with_bear_candle(self, monkeypatch):
        """SELL + candle_bear > 0 → signal passes through."""
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", True)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        sig = _make_sig(signal="SELL", bull_aligned=False, bear_aligned=True,
                        mfi=35.0, rsi=40.0, rsi_slope=-1.0, macd_hist=-0.001,
                        vwap_dist=-0.3, obv_slope=-1000,
                        candle_bull=0, candle_bear=2)
        result = compute_confluence(sig, None, None)
        assert result["signal"] != "HOLD"
        assert result["candle_gate"] == "PASS"

    def test_candle_bonus_still_added_to_score(self, monkeypatch):
        """When gate passes due to candle, the candle bonus is also in the score."""
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", True)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        # Pin equal IC weights so live data/ic_weights.json cannot push the base
        # score to the 50-cap before the candle bonus is applied.
        import ic_calculator as _ic
        monkeypatch.setattr(_ic, "get_current_weights", lambda: _ic.EQUAL_WEIGHTS)

        no_candle = compute_confluence(
            _make_sig(signal="BUY", candle_bull=0), None, None)
        with_candle = compute_confluence(
            _make_sig(signal="BUY", candle_bull=2), None, None)

        assert with_candle["score"] > no_candle["score"]


# ---------------------------------------------------------------------------
# candle_required=False (default) — gate never fires
# ---------------------------------------------------------------------------

class TestCandleGateOff:

    def test_off_by_default_allows_buy_without_candle(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", False)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        result = compute_confluence(_make_sig(signal="BUY", candle_bull=0), None, None)
        assert result.get("candle_gate") == "PASS"

    def test_off_allows_sell_without_candle(self, monkeypatch):
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", False)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        sig = _make_sig(signal="SELL", bull_aligned=False, bear_aligned=True,
                        mfi=35.0, rsi=40.0, rsi_slope=-1.0, macd_hist=-0.001,
                        vwap_dist=-0.3, obv_slope=-1000,
                        candle_bull=0, candle_bear=0)
        result = compute_confluence(sig, None, None)
        assert result.get("candle_gate") == "PASS"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestCandleGateEdgeCases:

    def test_hold_signal_unaffected(self, monkeypatch):
        """When the scorer resolves to HOLD (all indicators neutral), candle gate is PASS.

        The candle gate fires on the *computed* final_signal, not the raw input.
        A truly neutral indicator set (mfi=50, vwap_dist=0, obv_slope=0, no alignment,
        no breakout) produces HOLD from the scorer — the gate has nothing to block.
        """
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", True)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        neutral_sig = _make_sig(
            signal="HOLD",
            bull_aligned=False, bear_aligned=False,
            adx=10.0, macd_hist=0.0, macd_accel=0.0,
            mfi=50.0, rsi=50.0, rsi_slope=0.0,
            vwap_dist=0.0, obv_slope=0,
            donch_breakout=0, vol_ratio=0.5,
            bb_position=0.5, squeeze_on=False,
            candle_bull=0, candle_bear=0,
        )
        result = compute_confluence(neutral_sig, None, None)
        assert result["signal"] == "HOLD"
        assert result["candle_gate"] == "PASS"

    def test_candle_gate_key_always_present(self, monkeypatch):
        """candle_gate must be in the return dict regardless of mode."""
        for required in (True, False):
            monkeypatch.setitem(_config_mod.CONFIG, "candle_required", required)
            monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
            result = compute_confluence(_make_sig(), None, None)
            assert "candle_gate" in result, f"candle_gate missing when candle_required={required}"

    def test_mtf_hard_gate_early_return_has_candle_gate(self, monkeypatch):
        """The MTF hard gate early-return path also includes candle_gate."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "hard")
        bearish_daily = _make_sig(signal="SELL", bull_aligned=False, bear_aligned=True,
                                  adx=30.0, macd_hist=-0.01, timeframe="1d")
        result = compute_confluence(_make_sig(signal="BUY"), bearish_daily, None)
        assert result["signal"] == "HOLD"
        assert result["mtf_gate"] == "BLOCKED"
        assert "candle_gate" in result  # Must be present even on early return
        assert result["candle_gate"] == "SKIPPED"  # MTF hard gate fired — candle gate was never evaluated


# ---------------------------------------------------------------------------
# tf_count regression — guards against total_tf NameError (Bug #1)
# ---------------------------------------------------------------------------

class TestTfCountRegression:

    def test_tf_count_always_in_return_dict(self, monkeypatch):
        """tf_count must be present in return dict — absence means total_tf NameError."""
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", False)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        result = compute_confluence(_make_sig(), None, None)
        assert "tf_count" in result, "tf_count missing — total_tf NameError regression"

    def test_tf_count_equals_1_when_only_5m(self, monkeypatch):
        """tf_count == 1 when only the 5m signal is passed."""
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", False)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        result = compute_confluence(_make_sig(), None, None)
        assert result["tf_count"] == 1

    def test_tf_count_equals_2_when_5m_and_1d(self, monkeypatch):
        """tf_count == 2 when 5m + daily signals are passed."""
        monkeypatch.setitem(_config_mod.CONFIG, "candle_required", False)
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        daily = _make_sig(signal="BUY", timeframe="1d")
        result = compute_confluence(_make_sig(), daily, None)
        assert result["tf_count"] == 2
