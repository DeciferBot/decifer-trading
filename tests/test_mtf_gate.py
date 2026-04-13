"""Tests for signals.timeframe_alignment_check() and MTF gate in compute_confluence().

Covers:
- Hard gate blocks entry when daily opposes 5m direction
- Soft gate penalises score but doesn't block
- Gate respects ADX minimum (weak trends don't gate)
- Weekly gate (optional, controlled by mtf_require_weekly)
- HOLD signals pass through without gating
- Neutral daily trend doesn't trigger gate
- Gate metadata propagates through to return dict

NOTE: Relies on conftest.py for all dependency stubbing and path setup.
"""

import os
import sys

import pytest

# conftest.py handles all module stubs (ib_async, yfinance, etc.)
# Just ensure project root is on path so signals imports cleanly
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config as _config_mod

# Remove any stale stub so we can import the REAL signals module
# (test_bot.py replaces signals in sys.modules with a bare stub during collection)
if "signals" in sys.modules and not hasattr(sys.modules["signals"], "__file__"):
    del sys.modules["signals"]

from signals import compute_confluence, timeframe_alignment_check

# ---------------------------------------------------------------------------
# Fixtures: synthetic signal dicts mimicking compute_indicators() output
# ---------------------------------------------------------------------------


def _make_sig(
    signal="BUY",
    bull_aligned=True,
    bear_aligned=False,
    adx=30.0,
    macd_hist=0.001,
    mfi=60.0,
    rsi=55.0,
    rsi_slope=1.0,
    macd_accel=0.0001,
    atr=1.5,
    vol_ratio=1.2,
    bb_position=0.6,
    bb_width=0.04,
    squeeze_on=False,
    squeeze_intensity=0.0,
    vwap_dist=0.3,
    obv_slope=1000,
    donch_breakout=0,
    candle_bull=0,
    candle_bear=0,
    variance_ratio=1.0,
    ou_halflife=999.0,
    zscore=0.0,
    adf_pvalue=1.0,
    symbol="TEST",
    timeframe="5m",
    price=150.0,
):
    """Build a synthetic signal dict matching compute_indicators() output."""
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": price,
        "ema_fast": price + 1 if bull_aligned else price - 1,
        "ema_slow": price,
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


@pytest.fixture
def bullish_5m():
    """5m signal saying BUY with strong alignment."""
    return _make_sig(signal="BUY", bull_aligned=True, bear_aligned=False, timeframe="5m")


@pytest.fixture
def bearish_5m():
    """5m signal saying SELL with strong alignment."""
    return _make_sig(
        signal="SELL",
        bull_aligned=False,
        bear_aligned=True,
        mfi=35.0,
        rsi=40.0,
        rsi_slope=-1.0,
        macd_hist=-0.001,
        vwap_dist=-0.3,
        obv_slope=-1000,
        timeframe="5m",
    )


@pytest.fixture
def bullish_daily():
    """Daily signal: clearly bullish trend (EMA aligned, strong ADX)."""
    return _make_sig(signal="BUY", bull_aligned=True, bear_aligned=False, adx=30.0, macd_hist=0.01, timeframe="1d")


@pytest.fixture
def bearish_daily():
    """Daily signal: clearly bearish trend (EMA aligned, strong ADX)."""
    return _make_sig(signal="SELL", bull_aligned=False, bear_aligned=True, adx=30.0, macd_hist=-0.01, timeframe="1d")


@pytest.fixture
def neutral_daily():
    """Daily signal: no clear trend (low ADX, no EMA alignment)."""
    return _make_sig(signal="HOLD", bull_aligned=False, bear_aligned=False, adx=15.0, macd_hist=0.0, timeframe="1d")


@pytest.fixture
def weak_bearish_daily():
    """Daily signal: bearish EMA alignment but weak ADX (below gate threshold)."""
    return _make_sig(signal="SELL", bull_aligned=False, bear_aligned=True, adx=15.0, macd_hist=-0.001, timeframe="1d")


@pytest.fixture
def bullish_weekly():
    return _make_sig(signal="BUY", bull_aligned=True, bear_aligned=False, adx=25.0, timeframe="1w")


@pytest.fixture
def bearish_weekly():
    return _make_sig(signal="SELL", bull_aligned=False, bear_aligned=True, adx=25.0, timeframe="1w")


# ---------------------------------------------------------------------------
# timeframe_alignment_check() — unit tests
# ---------------------------------------------------------------------------


class TestTimeframeAlignmentCheck:
    def test_aligned_bull_5m_bull_daily(self, bullish_5m, bullish_daily):
        """5m BUY + daily bullish = aligned."""
        result = timeframe_alignment_check(bullish_5m, bullish_daily, None)
        assert result["aligned"] is True
        assert result["daily_confirms"] is True
        assert result["daily_trend"] == "BULL"

    def test_conflict_bull_5m_bear_daily(self, bullish_5m, bearish_daily):
        """5m BUY + daily bearish = conflict."""
        result = timeframe_alignment_check(bullish_5m, bearish_daily, None)
        assert result["aligned"] is False
        assert result["daily_confirms"] is False
        assert result["gate_applies"] is True
        assert "BEARISH" in result["conflict"]

    def test_conflict_bear_5m_bull_daily(self, bearish_5m, bullish_daily):
        """5m SELL + daily bullish = conflict."""
        result = timeframe_alignment_check(bearish_5m, bullish_daily, None)
        assert result["aligned"] is False
        assert result["daily_confirms"] is False

    def test_weak_adx_does_not_gate(self, bullish_5m, weak_bearish_daily):
        """Daily bearish but ADX < threshold → gate should NOT apply."""
        result = timeframe_alignment_check(bullish_5m, weak_bearish_daily, None)
        # Gate doesn't apply because ADX is too low
        assert result["gate_applies"] is False
        # So even though daily is bearish, alignment is True (gate is off)
        assert result["aligned"] is True

    def test_neutral_daily_does_not_gate(self, bullish_5m, neutral_daily):
        """Daily neutral (no EMA alignment) → no conflict."""
        result = timeframe_alignment_check(bullish_5m, neutral_daily, None)
        assert result["aligned"] is True
        assert result["daily_trend"] in ("NEUTRAL", "LEAN_BULL", "LEAN_BEAR")

    def test_no_daily_data_passes(self, bullish_5m):
        """No daily data available → can't gate, should pass."""
        result = timeframe_alignment_check(bullish_5m, None, None)
        assert result["aligned"] is True

    def test_hold_signal_passes(self, neutral_daily, bearish_daily):
        """5m HOLD signal → no entry to gate, should pass."""
        hold_5m = _make_sig(signal="HOLD", bull_aligned=False, bear_aligned=False)
        result = timeframe_alignment_check(hold_5m, bearish_daily, None)
        assert result["aligned"] is True

    def test_weekly_gate_when_enabled(self, bullish_5m, bullish_daily, bearish_weekly, monkeypatch):
        """Weekly bearish + mtf_require_weekly=True → conflict."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_require_weekly", True)
        result = timeframe_alignment_check(bullish_5m, bullish_daily, bearish_weekly)
        assert result["weekly_confirms"] is False
        assert result["aligned"] is False

    def test_weekly_gate_ignored_when_disabled(self, bullish_5m, bullish_daily, bearish_weekly, monkeypatch):
        """Weekly bearish + mtf_require_weekly=False → no conflict."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_require_weekly", False)
        result = timeframe_alignment_check(bullish_5m, bullish_daily, bearish_weekly)
        assert result["weekly_confirms"] is True
        assert result["aligned"] is True


# ---------------------------------------------------------------------------
# compute_confluence() — hard gate mode
# ---------------------------------------------------------------------------


class TestComputeConfluenceHardGate:
    def test_hard_gate_blocks_misaligned(self, bullish_5m, bearish_daily, monkeypatch):
        """Hard gate: 5m BUY + daily BEAR → score=0, signal=HOLD."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "hard")
        result = compute_confluence(bullish_5m, bearish_daily, None)
        assert result["signal"] == "HOLD"
        assert result["score"] == 0
        assert result["mtf_gate"] == "BLOCKED"
        assert len(result["mtf_conflict"]) > 0

    def test_hard_gate_allows_aligned(self, bullish_5m, bullish_daily, monkeypatch):
        """Hard gate: 5m BUY + daily BULL → passes, score > 0."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "hard")
        result = compute_confluence(bullish_5m, bullish_daily, None)
        assert result["signal"] != "HOLD"
        assert result["score"] > 0
        assert result["mtf_gate"] == "PASS"

    def test_hard_gate_blocks_bear_5m_vs_bull_daily(self, bearish_5m, bullish_daily, monkeypatch):
        """Hard gate: 5m SELL + daily BULL → blocked."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "hard")
        result = compute_confluence(bearish_5m, bullish_daily, None)
        assert result["signal"] == "HOLD"
        assert result["score"] == 0


# ---------------------------------------------------------------------------
# compute_confluence() — soft gate mode
# ---------------------------------------------------------------------------


class TestComputeConfluenceSoftGate:
    def test_soft_gate_penalises_misaligned(self, bullish_5m, bearish_daily, monkeypatch):
        """Soft gate: misaligned → score reduced by penalty, but not blocked."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "soft")
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_penalty_points", 8)
        # Pin equal IC weights so live data/ic_weights.json cannot inflate the
        # pre-cap score above 50, which would mask the penalty after capping.
        import ic_calculator as _ic

        monkeypatch.setattr(_ic, "get_current_weights", lambda: _ic.EQUAL_WEIGHTS)

        # Get aligned score for comparison
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        baseline = compute_confluence(bullish_5m, bearish_daily, None)
        baseline_score = baseline["score"]

        # Now with soft gate
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "soft")
        result = compute_confluence(bullish_5m, bearish_daily, None)
        assert result["mtf_gate"] == "PENALISED"
        assert result["score"] <= max(0, baseline_score - 8)

    def test_soft_gate_no_penalty_when_aligned(self, bullish_5m, bullish_daily, monkeypatch):
        """Soft gate: aligned → no penalty applied."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "soft")
        result = compute_confluence(bullish_5m, bullish_daily, None)
        assert result["mtf_gate"] == "PASS"

    def test_soft_gate_score_never_negative(self, bullish_5m, bearish_daily, monkeypatch):
        """Soft gate: even with large penalty, score floors at 0."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "soft")
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_penalty_points", 99)
        result = compute_confluence(bullish_5m, bearish_daily, None)
        assert result["score"] >= 0


# ---------------------------------------------------------------------------
# compute_confluence() — gate off mode (legacy behaviour)
# ---------------------------------------------------------------------------


class TestComputeConfluenceGateOff:
    def test_off_mode_no_blocking(self, bullish_5m, bearish_daily, monkeypatch):
        """Gate off: misaligned → no blocking, no penalty."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "off")
        result = compute_confluence(bullish_5m, bearish_daily, None)
        assert result["signal"] != "HOLD" or result["score"] > 0
        assert result.get("mtf_gate", "PASS") == "PASS"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestMTFGateEdgeCases:
    def test_strong_buy_signal_gated(self, bearish_daily, monkeypatch):
        """Even STRONG_BUY should be blocked by hard gate when daily is bearish."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "hard")
        strong_5m = _make_sig(signal="STRONG_BUY", bull_aligned=True, vol_ratio=2.5, mfi=70.0)
        result = compute_confluence(strong_5m, bearish_daily, None)
        assert result["signal"] == "HOLD"
        assert result["score"] == 0

    def test_weak_buy_signal_gated(self, bearish_daily, monkeypatch):
        """WEAK_BUY should also be caught by gate (contains 'BUY')."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "hard")
        weak_5m = _make_sig(signal="WEAK_BUY", bull_aligned=True)
        result = compute_confluence(weak_5m, bearish_daily, None)
        assert result["signal"] == "HOLD"

    def test_adx_threshold_configurable(self, bullish_5m, monkeypatch):
        """Changing mtf_adx_min_for_gate changes when gate fires."""
        # Daily bearish with ADX=22
        daily = _make_sig(signal="SELL", bull_aligned=False, bear_aligned=True, adx=22.0, timeframe="1d")

        # Default threshold (20) → gate should fire
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_adx_min_for_gate", 20)
        result = timeframe_alignment_check(bullish_5m, daily, None)
        assert result["gate_applies"] is True

        # Raise threshold to 25 → gate should NOT fire
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_adx_min_for_gate", 25)
        result = timeframe_alignment_check(bullish_5m, daily, None)
        assert result["gate_applies"] is False

    def test_gate_metadata_in_return(self, bullish_5m, bearish_daily, monkeypatch):
        """Verify mtf_gate, mtf_conflict, mtf_daily_trend in return dict."""
        monkeypatch.setitem(_config_mod.CONFIG, "mtf_gate_mode", "soft")
        result = compute_confluence(bullish_5m, bearish_daily, None)
        assert "mtf_gate" in result
        assert "mtf_conflict" in result
        assert "mtf_daily_trend" in result
        assert result["mtf_daily_trend"] == "BEAR"
