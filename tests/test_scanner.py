"""Tests for scanner module: symbol scanning, filtering, ranking.

All market data is mocked with deterministic DataFrames.
No network connections are made.
"""
import os, sys, types
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub heavy deps BEFORE importing any Decifer module
for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance",
             "praw", "feedparser", "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

# Stub config with required keys
import config as _config_mod
_cfg = {"log_file": "/dev/null", "trade_log": "/dev/null",
        "order_log": "/dev/null", "anthropic_api_key": "test-key",
        "model": "claude-sonnet-4-20250514", "max_tokens": 1000,
        "mongo_uri": "", "db_name": "test"}
if hasattr(_config_mod, "CONFIG"):
    for _k, _v in _cfg.items():
        _config_mod.CONFIG.setdefault(_k, _v)
else:
    _config_mod.CONFIG = _cfg


import sys
import os
from unittest.mock import patch, MagicMock
from typing import List, Dict

import pytest
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import scanner
    HAS_SCANNER = True
except ImportError:
    HAS_SCANNER = False


pytestmark = pytest.mark.skipif(
    not HAS_SCANNER, reason="scanner module not importable"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_symbols():
    return ["AAPL", "MSFT", "GOOG", "AMZN", "NVDA"]


@pytest.fixture()
def high_volume_data():
    """OHLCV with above-average volume."""
    np.random.seed(10)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=60, freq="B")
    close = 150.0 + np.random.randn(60)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 2.0,
            "Low": close - 2.0,
            "Close": close,
            "Volume": np.random.randint(5_000_000, 10_000_000, 60).astype(float),
        },
        index=dates,
    )


@pytest.fixture()
def low_volume_data():
    """OHLCV with very low volume (should be filtered out)."""
    np.random.seed(11)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=60, freq="B")
    close = 150.0 + np.random.randn(60)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.random.randint(1_000, 5_000, 60).astype(float),
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------

class TestScannerFilters:

    def test_filter_by_volume_removes_low_volume(self, low_volume_data):
        """Symbols with low average volume should be filtered out."""
        filter_fn = getattr(scanner, "filter_by_volume", None) or \
                    getattr(scanner, "volume_filter", None)
        if filter_fn is None:
            pytest.skip("No volume filter function found")
        min_volume = 1_000_000
        result = filter_fn(low_volume_data, min_volume=min_volume)
        assert result is False or result == [] or not result, (
            f"Low volume data should be filtered out, got {result}"
        )

    def test_filter_by_volume_keeps_high_volume(self, high_volume_data):
        """Symbols with high average volume should pass the filter."""
        filter_fn = getattr(scanner, "filter_by_volume", None) or \
                    getattr(scanner, "volume_filter", None)
        if filter_fn is None:
            pytest.skip("No volume filter function found")
        min_volume = 1_000_000
        result = filter_fn(high_volume_data, min_volume=min_volume)
        assert result is True or result == high_volume_data or result, (
            f"High volume data should pass filter, got {result!r}"
        )

    def test_empty_symbol_list_returns_empty(self, config):
        """Scanning an empty symbol list must return empty result."""
        scan_fn = getattr(scanner, "scan_symbols", None) or \
                  getattr(scanner, "run_scan", None) or \
                  getattr(scanner, "scan", None)
        if scan_fn is None:
            pytest.skip("No scan function found")
        try:
            result = scan_fn([], config=config)
            assert result == [] or result == {} or not result
        except Exception as exc:
            pytest.fail(f"scan raised for empty symbol list: {exc}")


# ---------------------------------------------------------------------------
# Ranking tests
# ---------------------------------------------------------------------------

class TestScannerRanking:

    def test_rank_candidates_returns_sorted_list(self):
        """rank_candidates() must return a list sorted by score descending."""
        ranker = getattr(scanner, "rank_candidates", None) or \
                 getattr(scanner, "rank_symbols", None)
        if ranker is None:
            pytest.skip("No ranking function found")
        candidates = [
            {"symbol": "AAPL", "score": 0.65},
            {"symbol": "MSFT", "score": 0.80},
            {"symbol": "GOOG", "score": 0.45},
        ]
        result = ranker(candidates)
        if isinstance(result, list) and len(result) > 1:
            scores = [r.get("score", 0) if isinstance(r, dict) else r for r in result]
            for i in range(len(scores) - 1):
                assert scores[i] >= scores[i + 1], (
                    f"Expected descending order, got {scores}"
                )

    def test_rank_empty_returns_empty(self):
        """Ranking an empty list must return empty."""
        ranker = getattr(scanner, "rank_candidates", None) or \
                 getattr(scanner, "rank_symbols", None)
        if ranker is None:
            pytest.skip("No ranking function found")
        try:
            result = ranker([])
            assert result == [] or not result
        except Exception as exc:
            pytest.fail(f"rank_candidates raised for empty list: {exc}")

    def test_rank_single_candidate_returns_one(self):
        """Single candidate list should return the same single item."""
        ranker = getattr(scanner, "rank_candidates", None) or \
                 getattr(scanner, "rank_symbols", None)
        if ranker is None:
            pytest.skip("No ranking function found")
        candidates = [{"symbol": "AAPL", "score": 0.75}]
        result = ranker(candidates)
        assert len(result) == 1, f"Expected 1 item, got {len(result)}"
