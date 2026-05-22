"""
Tests for options_provider.py — real options flow metrics for unusual-volume detection.

Tests 1-11 cover:
  - No synthetic OI anywhere in production
  - No yfinance import in options files
  - bid_size + ask_size never labelled as traded volume
  - FMP classified as NOT_USABLE_FOR_OPTIONS
  - OptionsFlowData provenance fields
  - Alpaca dailyBar.v → real volume (not quote size)
  - quote_size column label
  - Null provider handling
"""

from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Test 1: No production code contains `oi = volume * 5` ─────────────

def test_no_synthetic_oi():
    """Verify the synthetic OI hack is removed from all production files."""
    # Exclude test files themselves (they contain the string as a literal in assertions)
    prod_files = [
        p for p in pathlib.Path(".").rglob("*.py")
        if "test" not in p.parts
        and "test" not in p.stem
        and "archive" not in str(p)
        and ".claude" not in str(p)
        and "chief-decifer" not in str(p).lower()
        and "venv" not in str(p)
        and "site-packages" not in str(p)
    ]
    # The pattern to detect: assignment where oi is set to volume * 5
    # We match the actual code pattern, not string literals that describe it
    import re
    synthetic_oi_pattern = re.compile(r"\boi\s*=\s*volume\s*\*\s*5\b")
    for f in prod_files:
        try:
            src = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        assert not synthetic_oi_pattern.search(src), f"{f}: found synthetic OI assignment (oi = volume * 5)"


# ── Test 2: No production code imports yfinance ────────────────────────

def test_no_yfinance_import():
    """Verify yfinance is not imported in any options module."""
    options_files = [
        "options_provider.py",
        "options_scanner.py",
        "alpaca_options.py",
        "options_entries.py",
        "expression_router.py",
    ]
    for fn in options_files:
        p = pathlib.Path(fn)
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        assert "import yfinance" not in src, f"{fn}: imports yfinance"
        assert "from yfinance" not in src, f"{fn}: imports yfinance"
        # Also check 'import yf' style
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("import yf") and ("yf " in stripped or stripped == "import yf"):
                pytest.fail(f"{fn}: imports yfinance as yf: {line!r}")


# ── Test 3: bid_size + ask_size never labelled as traded volume ─────────

def test_quote_size_not_labelled_as_volume():
    """Ensure the old pattern of using quote size as volume is gone."""
    for fn in ["alpaca_options.py", "options_provider.py", "options_scanner.py"]:
        p = pathlib.Path(fn)
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        # The old pattern that was the root cause of the bug:
        assert "bid_size or 0) + (snap.latest_quote.ask_size" not in src, (
            f"{fn}: old quote-size-as-volume pattern found"
        )
        # Also check the SDK attribute form
        assert "snap.latest_quote.bid_size" not in src, (
            f"{fn}: snap.latest_quote.bid_size used — this is quote liquidity, not volume"
        )


# ── Test 4: FMP classified as NOT_USABLE_FOR_OPTIONS ──────────────────

def test_fmp_provider_status():
    """FMP audit result must be recorded as a module constant."""
    from options_provider import FMP_PROVIDER_STATUS
    assert FMP_PROVIDER_STATUS == "NOT_USABLE_FOR_OPTIONS"


# ── Test 5: OptionsFlowData has correct provider fields ───────────────

def test_options_flow_data_provenance():
    """OptionsFlowData dataclass must capture full provenance."""
    from options_provider import OptionsFlowData
    fd = OptionsFlowData(
        symbol="TEST",
        expiry="2026-06-20",
        dte=28,
        call_volume=500.0,
        call_volume_source="alpaca_rest_dailyBar",
        call_trade_count=30.0,
        call_trade_count_source="alpaca_rest_dailyBar",
        call_prev_volume=200.0,
        call_prev_volume_source="alpaca_rest_prevDailyBar",
        call_open_interest=None,
        call_open_interest_source="unavailable",
        put_volume=200.0,
        put_volume_source="alpaca_rest_dailyBar",
        put_trade_count=15.0,
        put_trade_count_source="alpaca_rest_dailyBar",
        put_prev_volume=100.0,
        put_prev_volume_source="alpaca_rest_prevDailyBar",
        put_open_interest=None,
        put_open_interest_source="unavailable",
        provider="alpaca_rest_dailyBar",
        provider_status="PARTIAL_FLOW",
        flow_definition="VOLUME_EXPANSION",
        provider_timestamp="2026-05-22T18:00:00Z",
        data_quality="REAL",
        flow_metrics_available=True,
    )
    assert fd.provider == "alpaca_rest_dailyBar"
    assert fd.provider_status == "PARTIAL_FLOW"
    assert fd.flow_definition == "VOLUME_EXPANSION"
    assert fd.call_open_interest is None
    assert fd.call_open_interest_source == "unavailable"
    assert fd.put_open_interest is None
    assert fd.put_open_interest_source == "unavailable"
    assert fd.flow_metrics_available is True
    assert fd.data_quality == "REAL"


# ── Test 6: Alpaca REST dailyBar.v maps to real volume ────────────────

def test_alpaca_dailybar_v_is_real_volume():
    """get_all_chains must use dailyBar.v as volume, not quote size."""
    raw_snap = {
        "TEST260620C00100000": {
            "dailyBar": {
                "v": 300, "n": 25, "c": 10.0, "h": 11.0,
                "l": 9.0, "o": 9.5, "t": "2026-05-22T04:00:00Z", "vw": 10.2,
            },
            "prevDailyBar": {
                "v": 100, "n": 10, "c": 9.0, "h": 9.5,
                "l": 8.5, "o": 8.8, "t": "2026-05-21T04:00:00Z", "vw": 9.0,
            },
            "latestQuote": {"bp": 9.5, "ap": 10.5, "bs": 50, "as": 60},
            "latestTrade": {"p": 10.0, "s": 1, "t": "2026-05-22T15:00:00Z"},
            "greeks": {"delta": 0.5, "gamma": 0.02, "theta": -0.1, "vega": 0.08},
            "impliedVolatility": 0.30,
        }
    }
    mock_client = MagicMock()
    mock_client.get_option_chain.return_value = raw_snap

    import alpaca_options
    # _OptionChainRequest is bound at module load time; patch it to survive
    # environments where an earlier test stubs alpaca.data.requests without
    # OptionChainRequest (pre-existing test suite isolation issue).
    with patch.object(alpaca_options, "_OptionChainRequest", MagicMock()), \
         patch.object(alpaca_options, "_get_raw_client", return_value=mock_client):
        chains = alpaca_options.get_all_chains("TEST", 14, 45)

    assert len(chains) == 1
    calls = chains[0]["calls"]
    assert not calls.empty
    row = calls.iloc[0]

    assert row["volume"] == 300, f"volume must be dailyBar.v=300, got {row['volume']}"
    assert row["trade_count"] == 25, f"trade_count must be dailyBar.n=25, got {row['trade_count']}"
    assert row["prev_volume"] == 100, f"prev_volume must be prevDailyBar.v=100, got {row['prev_volume']}"
    assert row["openInterest"] is None, f"OI must be None (not synthetic), got {row['openInterest']}"
    assert row["volume_source"] == "alpaca_rest_dailyBar"


# ── Test 7: dailyBar.n maps to trade_count ─────────────────────────────

def test_alpaca_dailybar_n_is_trade_count():
    """dailyBar.n (trade count) must be stored separately from volume."""
    raw_snap = {
        "TEST260620C00100000": {
            "dailyBar": {
                "v": 500, "n": 42, "c": 10.0, "h": 11.0,
                "l": 9.0, "o": 9.5, "t": "2026-05-22T04:00:00Z", "vw": 10.2,
            },
            "latestQuote": {"bp": 9.5, "ap": 10.5, "bs": 10, "as": 10},
            "latestTrade": {"p": 10.0, "s": 1, "t": "2026-05-22T15:00:00Z"},
            "greeks": {"delta": 0.5},
            "impliedVolatility": 0.30,
        }
    }
    mock_client = MagicMock()
    mock_client.get_option_chain.return_value = raw_snap

    import alpaca_options
    with patch.object(alpaca_options, "_OptionChainRequest", MagicMock()), \
         patch.object(alpaca_options, "_get_raw_client", return_value=mock_client):
        chains = alpaca_options.get_all_chains("TEST", 14, 45)

    row = chains[0]["calls"].iloc[0]
    assert row["trade_count"] == 42, f"trade_count must be dailyBar.n=42, got {row['trade_count']}"
    assert row["volume"] == 500, f"volume must be dailyBar.v=500, got {row['volume']}"


# ── Test 8: prevDailyBar.v maps to prev_volume ─────────────────────────

def test_alpaca_prevdailybar_v_is_prev_volume():
    """prevDailyBar.v must appear in prev_volume column, not fabricated."""
    raw_snap = {
        "TEST260620P00100000": {
            "dailyBar": {
                "v": 200, "n": 18, "c": 5.0, "h": 5.5,
                "l": 4.5, "o": 4.8, "t": "2026-05-22T04:00:00Z", "vw": 5.0,
            },
            "prevDailyBar": {"v": 75, "n": 8},
            "latestQuote": {"bp": 4.8, "ap": 5.2, "bs": 20, "as": 15},
            "latestTrade": {"p": 5.0, "s": 1, "t": "2026-05-22T15:00:00Z"},
            "greeks": {"delta": -0.4},
            "impliedVolatility": 0.35,
        }
    }
    mock_client = MagicMock()
    mock_client.get_option_chain.return_value = raw_snap

    import alpaca_options
    with patch.object(alpaca_options, "_OptionChainRequest", MagicMock()), \
         patch.object(alpaca_options, "_get_raw_client", return_value=mock_client):
        chains = alpaca_options.get_all_chains("TEST", 14, 45)

    row = chains[0]["puts"].iloc[0]
    assert row["prev_volume"] == 75, f"prev_volume must be prevDailyBar.v=75, got {row['prev_volume']}"
    assert row["prev_volume_source"] == "alpaca_rest_prevDailyBar"


# ── Test 9: Null provider returns None ─────────────────────────────────

def test_null_provider_returns_none():
    """When raw client is unavailable, get_options_flow_data must return None."""
    import options_provider
    with patch.object(options_provider, "_get_raw_client", return_value=None):
        result = options_provider.get_options_flow_data("TEST", 14, 45)
    assert result is None


# ── Test 10: quote_size column label ──────────────────────────────────

def test_quote_size_column_label():
    """quote_size must exist as a separate column — distinct from volume."""
    raw_snap = {
        "TEST260620C00100000": {
            "dailyBar": {
                "v": 300, "n": 25, "c": 10.0, "h": 11.0,
                "l": 9.0, "o": 9.5, "t": "2026-05-22T04:00:00Z", "vw": 10.2,
            },
            "latestQuote": {"bp": 9.5, "ap": 10.5, "bs": 50, "as": 60},
            "latestTrade": {"p": 10.0, "s": 1, "t": "2026-05-22T15:00:00Z"},
            "greeks": {"delta": 0.5},
            "impliedVolatility": 0.30,
        }
    }
    mock_client = MagicMock()
    mock_client.get_option_chain.return_value = raw_snap

    import alpaca_options
    with patch.object(alpaca_options, "_OptionChainRequest", MagicMock()), \
         patch.object(alpaca_options, "_get_raw_client", return_value=mock_client):
        chains = alpaca_options.get_all_chains("TEST", 14, 45)

    row = chains[0]["calls"].iloc[0]
    assert "quote_size" in row.index, "quote_size column must exist"
    assert row["quote_size"] == 110, f"quote_size = bs(50) + as(60) = 110, got {row['quote_size']}"
    assert row["volume"] == 300, f"volume must be dailyBar.v=300 (not quote_size), got {row['volume']}"


# ── Test 11: Null provider produces correct scanner signal ─────────────

def test_null_provider_signal_has_skip_reason():
    """When options_provider returns None, scanner signal must reflect provider_status=NULL."""
    raw_chain = {
        "TEST260620C00100000": {
            "dailyBar": {
                "v": 10, "n": 2, "c": 10.0, "h": 10.0,
                "l": 10.0, "o": 10.0, "t": "2026-05-22T04:00:00Z", "vw": 10.0,
            },
            "latestQuote": {"bp": 9.5, "ap": 10.5, "bs": 5, "as": 5},
            "latestTrade": {"p": 10.0, "s": 1, "t": "2026-05-22T15:00:00Z"},
            "greeks": {"delta": 0.5},
            "impliedVolatility": 0.30,
        },
        "TEST260620P00100000": {
            "dailyBar": {
                "v": 8, "n": 1, "c": 5.0, "h": 5.0,
                "l": 5.0, "o": 5.0, "t": "2026-05-22T04:00:00Z", "vw": 5.0,
            },
            "latestQuote": {"bp": 4.8, "ap": 5.2, "bs": 3, "as": 3},
            "latestTrade": {"p": 5.0, "s": 1, "t": "2026-05-22T15:00:00Z"},
            "greeks": {"delta": -0.4},
            "impliedVolatility": 0.35,
        },
    }
    mock_raw_client = MagicMock()
    mock_raw_client.get_option_chain.return_value = raw_chain

    import options_provider
    import alpaca_options
    import options_scanner

    # Provider returns None — simulates unavailable raw client in options_provider
    with patch.object(options_provider, "_get_raw_client", return_value=None), \
         patch.object(alpaca_options, "_get_raw_client", return_value=mock_raw_client):
        result = options_scanner._analyse_symbol("TEST", regime=None)

    # Result may be None (not enough score) or have provider_status=NULL
    if result is not None:
        assert result["provider_status"] == "NULL", (
            f"provider_status must be NULL when flow provider unavailable, got {result['provider_status']}"
        )
        assert result["unusual_calls"] is False
        assert result["unusual_puts"] is False
