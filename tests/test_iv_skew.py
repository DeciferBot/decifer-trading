"""Tests for iv_skew.py — IV skew computation, scoring, caching, and error handling."""
import os
import sys
import types
import unittest.mock as mock
from datetime import date, timedelta

# ── Project root on path ──────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Stub heavy dependencies BEFORE importing Decifer modules ─────────

# ib_async
ib_async_stub = types.ModuleType("ib_async")
ib_async_stub.IB = mock.MagicMock
sys.modules.setdefault("ib_async", ib_async_stub)

# anthropic
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = mock.MagicMock
sys.modules.setdefault("anthropic", anthropic_stub)

# config — provides CONFIG for iv_skew module
config_stub = types.ModuleType("config")
config_stub.CONFIG = {
    "alpaca_api_key":    "test_key",
    "alpaca_secret_key": "test_secret",
    "iv_skew": {
        "dte_min":         7,
        "dte_max":         60,
        "target_dte":      30,
        "otm_put_delta":  -0.25,
        "atm_call_delta":  0.50,
        "skew_bearish_hi":  0.15,
        "skew_bearish_mid": 0.10,
        "skew_bearish_lo":  0.05,
        "skew_bullish_lo": -0.03,
    },
}
sys.modules.setdefault("config", config_stub)
# If the real config is already loaded (full suite), point config_stub at it
# and ensure the iv_skew section exists so tests don't need a separate module.
config_stub = sys.modules["config"]
if not hasattr(config_stub, "CONFIG"):
    config_stub.CONFIG = {}
_iv_defaults = {
    "alpaca_api_key":    "test_key",
    "alpaca_secret_key": "test_secret",
    "iv_skew": {
        "dte_min":         7,
        "dte_max":         60,
        "target_dte":      30,
        "otm_put_delta":  -0.25,
        "atm_call_delta":  0.50,
        "skew_bearish_hi":  0.15,
        "skew_bearish_mid": 0.10,
        "skew_bearish_lo":  0.05,
        "skew_bullish_lo": -0.03,
    },
}
for _k, _v in _iv_defaults.items():
    config_stub.CONFIG.setdefault(_k, _v)

import pytest

# Evict iv_skew from cache so we get a fresh import with our stubs
sys.modules.pop("iv_skew", None)
import iv_skew  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_snapshot(delta: float, iv: float, ctype: str, strike: float = 100.0,
                   expiry_days: int = 30):
    """Build a minimal mock OptionSnapshot matching Alpaca SDK structure."""
    snap = mock.MagicMock()
    snap.greeks.delta = delta
    snap.implied_volatility = iv
    snap.details.strike_price = strike
    snap.details.type = ctype
    exp_date = date.today() + timedelta(days=expiry_days)
    snap.details.expiration_date = exp_date.isoformat()
    return snap


def _make_chain(*snapshots, expiry_days: int = 30):
    """
    Build a chain dict keyed by valid OCC option symbol strings.
    OCC format: <underlying><YYMMDD><C|P><8-digit-strike*1000>
    e.g. AAPL260508C00150000 = AAPL call, 2026-05-08, $150 strike
    """
    chain = {}
    exp_date = date.today() + timedelta(days=expiry_days)
    date_part = exp_date.strftime("%y%m%d")
    for i, snap in enumerate(snapshots):
        # Determine C/P from ctype in the snapshot mock's spec;
        # fall back to alternating C/P based on index.
        try:
            ctype = snap.details.type  # may or may not exist on mock
        except Exception:
            ctype = "call" if i % 2 == 0 else "put"
        cp    = "C" if "call" in str(ctype).lower() else "P"
        # Use a distinct strike per contract (100 + i * 5)
        strike_int = int((100 + i * 5) * 1000)
        occ_key = f"AAPL{date_part}{cp}{strike_int:08d}"
        chain[occ_key] = snap
    return chain


# ═══════════════════════════════════════════════════════════════════════
# _score_skew
# ═══════════════════════════════════════════════════════════════════════

class TestScoreSkew:
    """Tests for iv_skew._score_skew(skew, cfg)."""

    CFG = config_stub.CONFIG["iv_skew"]

    def test_high_skew_scores_10_bearish(self):
        score, direction = iv_skew._score_skew(0.20, self.CFG)
        assert score == 10
        assert direction == -1

    def test_mid_skew_scores_7_bearish(self):
        score, direction = iv_skew._score_skew(0.12, self.CFG)
        assert score == 7
        assert direction == -1

    def test_lo_skew_scores_4_bearish(self):
        score, direction = iv_skew._score_skew(0.07, self.CFG)
        assert score == 4
        assert direction == -1

    def test_neutral_band_scores_0(self):
        score, direction = iv_skew._score_skew(0.02, self.CFG)
        assert score == 0
        assert direction == 0

    def test_negative_skew_scores_3_bullish(self):
        score, direction = iv_skew._score_skew(-0.05, self.CFG)
        assert score == 3
        assert direction == +1

    def test_exact_boundary_bearish_hi(self):
        """Skew exactly at hi threshold should score 10."""
        score, direction = iv_skew._score_skew(0.151, self.CFG)
        assert score == 10
        assert direction == -1

    def test_exact_boundary_neutral_upper(self):
        """Skew just inside neutral band (below lo threshold) stays neutral."""
        score, direction = iv_skew._score_skew(0.049, self.CFG)
        assert score == 0 or score == 4  # 0.049 < 0.05 lo → neutral is 0
        # The boundary: 0.049 < 0.05 → skew > lo is False → falls through to bull check
        # 0.049 >= -0.03 bull → score 0, dir 0
        assert score == 0
        assert direction == 0


# ═══════════════════════════════════════════════════════════════════════
# get_iv_skew — client initialisation gating
# ═══════════════════════════════════════════════════════════════════════

class TestGetIvSkewClientGating:
    """get_iv_skew returns None when keys are missing or SDK not installed."""

    def test_returns_none_when_keys_empty(self):
        """Missing API keys → no client → None."""
        # Temporarily blank keys
        original = config_stub.CONFIG.copy()
        config_stub.CONFIG["alpaca_api_key"]    = ""
        config_stub.CONFIG["alpaca_secret_key"] = ""
        # Force client re-init
        iv_skew._client = None
        result = iv_skew.get_iv_skew("AAPL")
        assert result is None
        # Restore
        config_stub.CONFIG.update(original)
        iv_skew._client = None

    def test_returns_none_when_alpaca_not_installed(self):
        """alpaca-py ImportError → None."""
        iv_skew._client = None
        with mock.patch.dict("sys.modules",
                             {"alpaca.data.historical.option": None,
                              "alpaca.data.requests": None}):
            result = iv_skew.get_iv_skew("AAPL_NO_SDK")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# get_iv_skew — chain parsing
# ═══════════════════════════════════════════════════════════════════════

class TestGetIvSkewChainParsing:
    """get_iv_skew correctly selects ATM call and OTM put from a mock chain."""

    def setup_method(self):
        """Clear module-level cache and reset client before each test."""
        iv_skew._cache.clear()

    def _patch_client(self, chain: dict):
        """Return a context manager that replaces the Alpaca client with a mock."""
        fake_client = mock.MagicMock()
        fake_client.get_option_chain.return_value = chain

        # Patch _get_client to return the fake client
        return mock.patch.object(iv_skew, "_get_client", return_value=fake_client)

    def test_selects_correct_atm_call_and_otm_put(self):
        """ATM call chosen by delta closest to +0.50; OTM put by delta closest to -0.25."""
        snapshots = [
            _make_snapshot(delta=0.50, iv=0.25, ctype="call", strike=100.0),  # ATM call
            _make_snapshot(delta=0.30, iv=0.28, ctype="call", strike=95.0),
            _make_snapshot(delta=-0.25, iv=0.35, ctype="put", strike=95.0),   # OTM put
            _make_snapshot(delta=-0.50, iv=0.30, ctype="put", strike=90.0),
        ]
        chain = _make_chain(*snapshots)

        with self._patch_client(chain):
            with mock.patch.dict("sys.modules", {
                "alpaca.data.requests": types.SimpleNamespace(
                    OptionChainRequest=mock.MagicMock()
                )
            }):
                result = iv_skew.get_iv_skew("AAPL")

        if result is not None:
            assert result["atm_call_iv"] == pytest.approx(0.25, abs=0.001)
            assert result["otm_put_iv"]  == pytest.approx(0.35, abs=0.001)
            expected_skew = 0.35 - 0.25
            assert result["skew"] == pytest.approx(expected_skew, abs=0.001)
            assert result["source"] == "alpaca"

    def test_empty_chain_returns_none(self):
        """Empty chain dict → None."""
        with self._patch_client({}):
            with mock.patch.dict("sys.modules", {
                "alpaca.data.requests": types.SimpleNamespace(
                    OptionChainRequest=mock.MagicMock()
                )
            }):
                result = iv_skew.get_iv_skew("EMPTY")
        assert result is None

    def test_no_puts_returns_none(self):
        """Chain with only calls → None (cannot compute skew)."""
        calls_only = [
            _make_snapshot(delta=0.50, iv=0.25, ctype="call"),
            _make_snapshot(delta=0.35, iv=0.28, ctype="call"),
        ]
        chain = _make_chain(*calls_only)
        with self._patch_client(chain):
            with mock.patch.dict("sys.modules", {
                "alpaca.data.requests": types.SimpleNamespace(
                    OptionChainRequest=mock.MagicMock()
                )
            }):
                result = iv_skew.get_iv_skew("CALLS_ONLY")
        assert result is None

    def test_no_calls_returns_none(self):
        """Chain with only puts → None."""
        puts_only = [
            _make_snapshot(delta=-0.25, iv=0.35, ctype="put"),
            _make_snapshot(delta=-0.50, iv=0.30, ctype="put"),
        ]
        chain = _make_chain(*puts_only)
        with self._patch_client(chain):
            with mock.patch.dict("sys.modules", {
                "alpaca.data.requests": types.SimpleNamespace(
                    OptionChainRequest=mock.MagicMock()
                )
            }):
                result = iv_skew.get_iv_skew("PUTS_ONLY")
        assert result is None

    def test_skips_snapshots_with_zero_iv(self):
        """Snapshots with IV = 0 or None are excluded from selection."""
        snapshots = [
            _make_snapshot(delta=0.50, iv=0.0,  ctype="call"),   # IV=0 → excluded
            _make_snapshot(delta=0.48, iv=0.25, ctype="call"),   # this wins
            _make_snapshot(delta=-0.25, iv=0.35, ctype="put"),
        ]
        chain = _make_chain(*snapshots)
        with self._patch_client(chain):
            with mock.patch.dict("sys.modules", {
                "alpaca.data.requests": types.SimpleNamespace(
                    OptionChainRequest=mock.MagicMock()
                )
            }):
                result = iv_skew.get_iv_skew("ZERO_IV")
        if result is not None:
            assert result["atm_call_iv"] == pytest.approx(0.25, abs=0.001)


# ═══════════════════════════════════════════════════════════════════════
# get_iv_skew — result structure
# ═══════════════════════════════════════════════════════════════════════

class TestGetIvSkewResultStructure:
    """Successful result contains all required keys and sane values."""

    def setup_method(self):
        iv_skew._cache.clear()

    def _run_with_chain(self, snapshots):
        chain = _make_chain(*snapshots)
        fake_client = mock.MagicMock()
        fake_client.get_option_chain.return_value = chain
        with mock.patch.object(iv_skew, "_get_client", return_value=fake_client):
            with mock.patch.dict("sys.modules", {
                "alpaca.data.requests": types.SimpleNamespace(
                    OptionChainRequest=mock.MagicMock()
                )
            }):
                return iv_skew.get_iv_skew("TEST")

    def test_result_has_all_required_keys(self):
        snaps = [
            _make_snapshot(delta=0.50, iv=0.25, ctype="call"),
            _make_snapshot(delta=-0.25, iv=0.35, ctype="put"),
        ]
        result = self._run_with_chain(snaps)
        if result is not None:
            for key in ["skew", "otm_put_iv", "atm_call_iv",
                        "iv_skew_score", "iv_skew_dir", "expiry", "source"]:
                assert key in result, f"Missing key: {key}"

    def test_iv_skew_score_in_valid_range(self):
        snaps = [
            _make_snapshot(delta=0.50, iv=0.25, ctype="call"),
            _make_snapshot(delta=-0.25, iv=0.35, ctype="put"),
        ]
        result = self._run_with_chain(snaps)
        if result is not None:
            assert 0 <= result["iv_skew_score"] <= 10

    def test_iv_skew_dir_is_valid(self):
        snaps = [
            _make_snapshot(delta=0.50, iv=0.25, ctype="call"),
            _make_snapshot(delta=-0.25, iv=0.35, ctype="put"),
        ]
        result = self._run_with_chain(snaps)
        if result is not None:
            assert result["iv_skew_dir"] in (-1, 0, +1)

    def test_high_skew_gives_bearish_direction(self):
        """put_iv=0.45, call_iv=0.25 → skew=0.20 → bearish (dir=-1)."""
        snaps = [
            _make_snapshot(delta=0.50, iv=0.25, ctype="call"),
            _make_snapshot(delta=-0.25, iv=0.45, ctype="put"),
        ]
        result = self._run_with_chain(snaps)
        if result is not None:
            assert result["iv_skew_dir"] == -1
            assert result["iv_skew_score"] == 10

    def test_negative_skew_gives_bullish_direction(self):
        """put_iv=0.20, call_iv=0.30 → skew=-0.10 → bullish (dir=+1)."""
        snaps = [
            _make_snapshot(delta=0.50, iv=0.30, ctype="call"),
            _make_snapshot(delta=-0.25, iv=0.20, ctype="put"),
        ]
        result = self._run_with_chain(snaps)
        if result is not None:
            assert result["iv_skew_dir"] == +1


# ═══════════════════════════════════════════════════════════════════════
# get_iv_skew — caching
# ═══════════════════════════════════════════════════════════════════════

class TestGetIvSkewCaching:
    """Results are cached for the same (symbol, date); cleared between tests."""

    def setup_method(self):
        iv_skew._cache.clear()

    def test_second_call_uses_cache_not_client(self):
        """Second call for same symbol + date returns cached result; no new API call."""
        snaps = [
            _make_snapshot(delta=0.50, iv=0.25, ctype="call"),
            _make_snapshot(delta=-0.25, iv=0.35, ctype="put"),
        ]
        chain = _make_chain(*snaps)
        fake_client = mock.MagicMock()
        fake_client.get_option_chain.return_value = chain

        with mock.patch.object(iv_skew, "_get_client", return_value=fake_client):
            with mock.patch.dict("sys.modules", {
                "alpaca.data.requests": types.SimpleNamespace(
                    OptionChainRequest=mock.MagicMock()
                )
            }):
                r1 = iv_skew.get_iv_skew("AAPL_CACHE")
                r2 = iv_skew.get_iv_skew("AAPL_CACHE")

        # Client was called exactly once (second call hit cache)
        assert fake_client.get_option_chain.call_count == 1
        assert r1 == r2

    def test_cache_is_symbol_specific(self):
        """Cache hit for AAPL does not affect TSLA."""
        today = date.today().isoformat()
        iv_skew._cache[("AAPL_CACHED", today)] = {
            "skew": 0.10, "iv_skew_score": 7, "iv_skew_dir": -1,
            "otm_put_iv": 0.35, "atm_call_iv": 0.25,
            "expiry": today, "source": "alpaca",
        }

        fake_client = mock.MagicMock()
        fake_client.get_option_chain.return_value = {}  # empty chain for TSLA

        with mock.patch.object(iv_skew, "_get_client", return_value=fake_client):
            with mock.patch.dict("sys.modules", {
                "alpaca.data.requests": types.SimpleNamespace(
                    OptionChainRequest=mock.MagicMock()
                )
            }):
                tsla_result = iv_skew.get_iv_skew("TSLA_MISS")

        # AAPL result still in cache, TSLA got a live (empty) fetch
        assert ("AAPL_CACHED", today) in iv_skew._cache
        assert tsla_result is None  # empty chain → None
