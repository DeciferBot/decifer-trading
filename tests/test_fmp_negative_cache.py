"""
tests/test_fmp_negative_cache.py

Tests for FMP negative caching (HTTP 402 and Error Message responses).
All tests mock requests.get — no real FMP calls are made.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

import fmp_client


# ── Helpers ────────────────────────────────────────────────────────────────────


def _http_error(status: int) -> requests.exceptions.HTTPError:
    resp = MagicMock()
    resp.status_code = status
    exc = requests.exceptions.HTTPError()
    exc.response = resp
    return exc


def _ok_response(data: object) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = data
    return m


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestFmpNegativeCache:
    def setup_method(self):
        fmp_client._neg_cache.clear()
        fmp_client._cache.clear()

    def test_fmp_402_writes_neg_cache_and_second_call_skips_network(self):
        with (
            patch.object(fmp_client, "_api_key", return_value="FAKE"),
            patch("fmp_client.requests.get", side_effect=_http_error(402)) as mock_get,
        ):
            result1 = fmp_client._get("test-endpoint", {"symbol": "AAPL"})
            result2 = fmp_client._get("test-endpoint", {"symbol": "AAPL"})

        assert result1 is None
        assert result2 is None
        # Network should be called exactly once — second call hits neg_cache
        assert mock_get.call_count == 1

    def test_fmp_402_neg_cache_ttl_is_24h(self):
        with (
            patch.object(fmp_client, "_api_key", return_value="FAKE"),
            patch("fmp_client.requests.get", side_effect=_http_error(402)),
        ):
            fmp_client._get("test-endpoint", {"symbol": "TTL"})

        cache_key = 'test-endpoint?{"symbol": "TTL"}'
        blocked_until = fmp_client._neg_cache.get(cache_key, 0.0)
        remaining = blocked_until - time.time()
        # Should be close to 24h (within ±60s tolerance)
        assert 23 * 3600 < remaining < 25 * 3600

    def test_fmp_error_message_writes_neg_cache(self):
        with (
            patch.object(fmp_client, "_api_key", return_value="FAKE"),
            patch("fmp_client.requests.get", return_value=_ok_response({"Error Message": "limit exceeded"})) as mock_get,
        ):
            result1 = fmp_client._get("test-endpoint", {"symbol": "ERR"})
            result2 = fmp_client._get("test-endpoint", {"symbol": "ERR"})

        assert result1 is None
        assert result2 is None
        assert mock_get.call_count == 1

    def test_fmp_error_message_neg_cache_ttl_is_4h(self):
        with (
            patch.object(fmp_client, "_api_key", return_value="FAKE"),
            patch("fmp_client.requests.get", return_value=_ok_response({"Error Message": "quota"})),
        ):
            fmp_client._get("test-endpoint", {"symbol": "ETTL"})

        cache_key = 'test-endpoint?{"symbol": "ETTL"}'
        blocked_until = fmp_client._neg_cache.get(cache_key, 0.0)
        remaining = blocked_until - time.time()
        # Should be close to 4h (within ±60s tolerance)
        assert 3 * 3600 < remaining < 5 * 3600

    def test_fmp_neg_cache_expires_and_retries_network(self):
        cache_key = 'test-endpoint?{"symbol": "EXP"}'
        fmp_client._neg_cache[cache_key] = time.time() - 1  # already expired

        good_data = [{"symbol": "EXP", "revenue": 1000}]
        with (
            patch.object(fmp_client, "_api_key", return_value="FAKE"),
            patch("fmp_client.requests.get", return_value=_ok_response(good_data)) as mock_get,
        ):
            result = fmp_client._get("test-endpoint", {"symbol": "EXP"})

        assert result == good_data
        assert mock_get.call_count == 1

    def test_fmp_402_log_contains_entitlement(self, caplog):
        import logging
        with (
            patch.object(fmp_client, "_api_key", return_value="FAKE"),
            patch("fmp_client.requests.get", side_effect=_http_error(402)),
            caplog.at_level(logging.WARNING, logger="decifer.fmp"),
        ):
            fmp_client._get("income-statement", {"symbol": "AAPL"})

        assert any("entitlement" in r.message for r in caplog.records)

    def test_fmp_5xx_does_not_write_neg_cache(self):
        with (
            patch.object(fmp_client, "_api_key", return_value="FAKE"),
            patch("fmp_client.requests.get", side_effect=_http_error(500)),
        ):
            fmp_client._get("test-endpoint", {"symbol": "5XX"})

        cache_key = 'test-endpoint?{"symbol": "5XX"}'
        assert cache_key not in fmp_client._neg_cache
