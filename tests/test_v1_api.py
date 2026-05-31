"""
Tests for Product API v1 — v1_auth.py + v1_api.py.

Covers:
  - Auth: missing key, invalid key, valid key, dev bypass
  - Rate limiting: token bucket exhaustion and refill
  - Symbol card: found, not found, invalid ticker format
  - Options flow cache: cache hit, cache miss, Alpaca failure
  - Data readers: drivers, exposures, candidate feed
  - Response field safety: no blocked fields in any response
"""
from __future__ import annotations

import json
import os
import time
import threading
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures — isolated Flask test client
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_module_caches():
    """Clear in-memory caches between tests."""
    import v1_api
    v1_api._nodes_cache = None
    yield
    v1_api._nodes_cache = None


@pytest.fixture()
def app_with_key(tmp_path, monkeypatch):
    """Flask test app with a single API key set."""
    monkeypatch.setenv("INTELLIGENCE_API_KEYS", "test-key-abc")
    monkeypatch.setenv("DECIFER_RUNTIME_MODE", "intelligence_cloud")
    monkeypatch.setenv("INTELLIGENCE_API_CACHE_DIR", str(tmp_path / "api_cache"))

    import importlib
    import v1_auth, v1_api
    importlib.reload(v1_auth)
    importlib.reload(v1_api)

    from flask import Flask
    app = Flask("test")
    app.register_blueprint(v1_api.v1_bp)
    app.testing = True
    return app


@pytest.fixture()
def app_dev(tmp_path, monkeypatch):
    """Flask test app in dev mode (no API keys set)."""
    monkeypatch.delenv("INTELLIGENCE_API_KEYS", raising=False)
    monkeypatch.setenv("DECIFER_RUNTIME_MODE", "paper_execution")

    import importlib
    import v1_auth, v1_api
    importlib.reload(v1_auth)
    importlib.reload(v1_api)

    from flask import Flask
    app = Flask("test")
    app.register_blueprint(v1_api.v1_bp)
    app.testing = True
    return app


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestAuth:
    def test_missing_key_returns_401(self, app_with_key):
        with app_with_key.test_client() as c:
            r = c.get("/v1/symbol/NVDA")
            assert r.status_code == 401
            body = r.get_json()
            assert body["error"]["code"] == 401
            assert "Missing API key" in body["error"]["message"]

    def test_invalid_key_returns_401(self, app_with_key):
        with app_with_key.test_client() as c:
            r = c.get("/v1/symbol/NVDA", headers={"X-API-Key": "wrong-key"})
            assert r.status_code == 401

    def test_query_param_key_accepted(self, app_with_key):
        with app_with_key.test_client() as c:
            with patch("v1_api._build_symbol_card", return_value=None):
                r = c.get("/v1/symbol/NVDA?api_key=test-key-abc")
                assert r.status_code == 404  # not 401 — key was accepted

    def test_health_requires_no_auth(self, app_with_key):
        with app_with_key.test_client() as c:
            with patch("v1_api._read_drivers", return_value={"mode": "test", "generated_at": "2026-01-01"}):
                r = c.get("/v1/health")
                assert r.status_code == 200

    def test_dev_bypass_no_key_needed(self, app_dev):
        with app_dev.test_client() as c:
            with patch("v1_api._build_symbol_card", return_value=None):
                r = c.get("/v1/symbol/AAPL")
                assert r.status_code == 404  # not 401


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_rate_limit_allows_normal_traffic(self, app_with_key):
        with app_with_key.test_client() as c:
            headers = {"X-API-Key": "test-key-abc"}
            with patch("v1_api._build_symbol_card", return_value=None):
                for _ in range(5):
                    r = c.get("/v1/symbol/AAPL", headers=headers)
                    assert r.status_code in (200, 404)

    def test_rate_limit_returns_429_on_exhaustion(self, monkeypatch):
        import v1_auth
        # Manually exhaust bucket
        key = "exhaustion-test-key"
        v1_auth._buckets[key] = {"tokens": 0.0, "last_refill": time.monotonic()}
        allowed, retry_after = v1_auth._consume_token(key)
        assert not allowed
        assert retry_after > 0

    def test_rate_limit_retry_after_header(self, app_with_key):
        import v1_auth
        key = "test-key-abc"
        # Force empty bucket
        v1_auth._buckets[key] = {"tokens": 0.0, "last_refill": time.monotonic()}
        with app_with_key.test_client() as c:
            r = c.get("/v1/symbol/NVDA", headers={"X-API-Key": key})
            assert r.status_code == 429
            assert "Retry-After" in r.headers

    def test_token_bucket_refills_over_time(self):
        import v1_auth
        key = "refill-test-key"
        # Exhaust
        v1_auth._buckets[key] = {"tokens": 0.0, "last_refill": time.monotonic() - 10.0}
        # After 10s, refill_rate * 10 tokens should be available
        allowed, _ = v1_auth._consume_token(key)
        assert allowed


# ---------------------------------------------------------------------------
# Ticker validation tests
# ---------------------------------------------------------------------------

class TestTickerValidation:
    def test_invalid_ticker_too_long(self, app_dev):
        with app_dev.test_client() as c:
            r = c.get("/v1/symbol/TOOLONGTICKER")
            assert r.status_code == 400

    def test_invalid_ticker_non_alpha(self, app_dev):
        with app_dev.test_client() as c:
            r = c.get("/v1/symbol/NV%DA")
            # Flask will reject the % as a URL encoding issue or pass it through
            assert r.status_code in (400, 404)

    def test_ticker_normalised_to_uppercase(self, app_dev):
        with app_dev.test_client() as c:
            with patch("v1_api._build_symbol_card") as mock_build:
                mock_build.return_value = None
                c.get("/v1/symbol/nvda")
                mock_build.assert_called_once_with("NVDA")


# ---------------------------------------------------------------------------
# Symbol card builder tests
# ---------------------------------------------------------------------------

MOCK_EXPOSURE = {
    "symbol": "NVDA",
    "label": "NVIDIA Corporation",
    "driver_id": "ai_capex_growth",
    "theme_id": "ai_energy_nuclear",
    "bucket_id": "ai_compute_accelerators_networking",
    "exposure_type": "direct_beneficiary",
    "confidence": 0.95,
    "reason_to_care": "NVIDIA dominates AI accelerator market.",
    "evidence_basis": "company_profile",
    "risk_note": "Export controls risk.",
    "status": "active",
    "last_reviewed": "2026-05-26",
}

MOCK_DRIVER_STATE = {
    "active_drivers": ["ai_capex_growth", "yields_falling"],
    "blocked_conditions": ["credit_stress_rising"],
    "generated_at": "2026-05-30T14:00:00Z",
    "mode": "live_market_data",
}

MOCK_CANDIDATE = {
    "symbol": "NVDA",
    "role": "direct_beneficiary",
    "reason_to_care": "NVDA is the leading AI chip maker.",
    "confidence": 0.90,
    "risk_flags": ["valuation", "crowding"],
    "theme": "ai_energy_nuclear",
    "driver": "ai_capex_growth",
    "generated_at": "2026-05-30T09:00:00Z",
}


class TestSymbolCard:
    def test_returns_none_for_unknown_symbol(self):
        import v1_api
        with patch("v1_api._read_exposures", return_value=[]):
            result = v1_api._build_symbol_card("UNKNOWN")
            assert result is None

    def test_returns_card_for_known_symbol(self):
        import v1_api
        with (
            patch("v1_api._read_exposures", return_value=[MOCK_EXPOSURE]),
            patch("v1_api._read_drivers", return_value=MOCK_DRIVER_STATE),
            patch("v1_api._read_candidate_for", return_value=None),
            patch("v1_api._options_flow_cached", return_value=None),
            patch("v1_api._load_nodes", return_value={}),
        ):
            card = v1_api._build_symbol_card("NVDA")
            assert card is not None
            assert card["symbol"] == "NVDA"
            assert card["api_version"] == "1"

    def test_themes_list_populated(self):
        import v1_api
        with (
            patch("v1_api._read_exposures", return_value=[MOCK_EXPOSURE]),
            patch("v1_api._read_drivers", return_value=MOCK_DRIVER_STATE),
            patch("v1_api._read_candidate_for", return_value=None),
            patch("v1_api._options_flow_cached", return_value=None),
            patch("v1_api._load_nodes", return_value={}),
        ):
            card = v1_api._build_symbol_card("NVDA")
            assert len(card["themes"]) == 1
            theme = card["themes"][0]
            assert theme["theme_id"] == "ai_energy_nuclear"
            assert theme["exposure_type"] == "direct_beneficiary"
            assert theme["confidence"] == 0.95
            assert theme["reason_to_care"] == "NVIDIA dominates AI accelerator market."

    def test_driver_active_flag_set_correctly(self):
        import v1_api
        with (
            patch("v1_api._read_exposures", return_value=[MOCK_EXPOSURE]),
            patch("v1_api._read_drivers", return_value=MOCK_DRIVER_STATE),
            patch("v1_api._read_candidate_for", return_value=None),
            patch("v1_api._options_flow_cached", return_value=None),
            patch("v1_api._load_nodes", return_value={}),
        ):
            card = v1_api._build_symbol_card("NVDA")
            # ai_capex_growth is in active_drivers → driver_active=True
            assert card["themes"][0]["driver_active"] is True

    def test_driver_active_false_when_not_active(self):
        import v1_api
        inactive_state = {**MOCK_DRIVER_STATE, "active_drivers": ["yields_falling"]}
        with (
            patch("v1_api._read_exposures", return_value=[MOCK_EXPOSURE]),
            patch("v1_api._read_drivers", return_value=inactive_state),
            patch("v1_api._read_candidate_for", return_value=None),
            patch("v1_api._options_flow_cached", return_value=None),
            patch("v1_api._load_nodes", return_value={}),
        ):
            card = v1_api._build_symbol_card("NVDA")
            assert card["themes"][0]["driver_active"] is False

    def test_intelligence_feed_populated_when_in_feed(self):
        import v1_api
        with (
            patch("v1_api._read_exposures", return_value=[MOCK_EXPOSURE]),
            patch("v1_api._read_drivers", return_value=MOCK_DRIVER_STATE),
            patch("v1_api._read_candidate_for", return_value=MOCK_CANDIDATE),
            patch("v1_api._options_flow_cached", return_value=None),
            patch("v1_api._load_nodes", return_value={}),
        ):
            card = v1_api._build_symbol_card("NVDA")
            assert card["intelligence_feed"] is not None
            assert card["intelligence_feed"]["in_feed"] is True
            assert card["intelligence_feed"]["role"] == "direct_beneficiary"
            assert card["intelligence_feed"]["confidence"] == 0.90

    def test_intelligence_feed_none_when_not_in_feed(self):
        import v1_api
        with (
            patch("v1_api._read_exposures", return_value=[MOCK_EXPOSURE]),
            patch("v1_api._read_drivers", return_value=MOCK_DRIVER_STATE),
            patch("v1_api._read_candidate_for", return_value=None),
            patch("v1_api._options_flow_cached", return_value=None),
            patch("v1_api._load_nodes", return_value={}),
        ):
            card = v1_api._build_symbol_card("NVDA")
            assert card["intelligence_feed"] is None

    def test_market_context_populated(self):
        import v1_api
        with (
            patch("v1_api._read_exposures", return_value=[MOCK_EXPOSURE]),
            patch("v1_api._read_drivers", return_value=MOCK_DRIVER_STATE),
            patch("v1_api._read_candidate_for", return_value=None),
            patch("v1_api._options_flow_cached", return_value=None),
            patch("v1_api._load_nodes", return_value={}),
        ):
            card = v1_api._build_symbol_card("NVDA")
            ctx = card["market_context"]
            assert "ai_capex_growth" in ctx["active_drivers"]
            assert "credit_stress_rising" in ctx["blocked_conditions"]
            assert ctx["drivers_mode"] == "live_market_data"

    def test_disclaimer_present(self):
        import v1_api
        with (
            patch("v1_api._read_exposures", return_value=[MOCK_EXPOSURE]),
            patch("v1_api._read_drivers", return_value=MOCK_DRIVER_STATE),
            patch("v1_api._read_candidate_for", return_value=None),
            patch("v1_api._options_flow_cached", return_value=None),
            patch("v1_api._load_nodes", return_value={}),
        ):
            card = v1_api._build_symbol_card("NVDA")
            assert "disclaimer" in card
            assert len(card["disclaimer"]) > 10

    def test_no_blocked_fields_in_response(self):
        import v1_api
        with (
            patch("v1_api._read_exposures", return_value=[MOCK_EXPOSURE]),
            patch("v1_api._read_drivers", return_value=MOCK_DRIVER_STATE),
            patch("v1_api._read_candidate_for", return_value=MOCK_CANDIDATE),
            patch("v1_api._options_flow_cached", return_value=None),
            patch("v1_api._load_nodes", return_value={}),
        ):
            card = v1_api._build_symbol_card("NVDA")
            card_json = json.dumps(card)
            for blocked in ["entry_price", "exit_price", "pnl", "order_id",
                            "position_size", "stop_price", "open_interest",
                            "implied_volatility", "delta", "gamma"]:
                assert f'"{blocked}"' not in card_json, f"Blocked field '{blocked}' found in response"


# ---------------------------------------------------------------------------
# Options flow cache tests
# ---------------------------------------------------------------------------

MOCK_FLOW = {
    "call_volume": 12400,
    "put_volume": 3200,
    "call_trade_count": 85,
    "call_expansion": 2.3,
    "put_expansion": 1.1,
    "unusual_calls": True,
    "unusual_puts": False,
    "unusual": True,
    "oi_available": False,
    "oi_note": "Open interest unavailable.",
    "provider": "alpaca_rest_dailyBar",
    "flow_definition": "VOLUME_EXPANSION",
    "data_ts": "2026-05-30T14:00:00Z",
}


class TestOptionsFlowCache:
    def test_cache_miss_calls_fetch(self, tmp_path, monkeypatch):
        import v1_api
        monkeypatch.setattr(v1_api, "_CACHE_DIR", tmp_path)
        with patch("v1_api._fetch_options_flow", return_value=MOCK_FLOW) as mock_fetch:
            result = v1_api._options_flow_cached("NVDA")
            mock_fetch.assert_called_once_with("NVDA")
            assert result == MOCK_FLOW

    def test_cache_hit_skips_fetch(self, tmp_path, monkeypatch):
        import v1_api
        monkeypatch.setattr(v1_api, "_CACHE_DIR", tmp_path)
        # Pre-populate cache
        cache_file = tmp_path / "NVDA_flow.json"
        cache_file.write_text(json.dumps(MOCK_FLOW))
        with patch("v1_api._fetch_options_flow") as mock_fetch:
            result = v1_api._options_flow_cached("NVDA")
            mock_fetch.assert_not_called()
            assert result["unusual"] is True

    def test_cache_expired_calls_fetch(self, tmp_path, monkeypatch):
        import v1_api
        monkeypatch.setattr(v1_api, "_CACHE_DIR", tmp_path)
        monkeypatch.setattr(v1_api, "_CACHE_TTL_SECONDS", 0)
        cache_file = tmp_path / "NVDA_flow.json"
        cache_file.write_text(json.dumps(MOCK_FLOW))
        with patch("v1_api._fetch_options_flow", return_value=MOCK_FLOW) as mock_fetch:
            v1_api._options_flow_cached("NVDA")
            mock_fetch.assert_called_once()

    def test_alpaca_failure_returns_none(self, tmp_path, monkeypatch):
        import v1_api
        monkeypatch.setattr(v1_api, "_CACHE_DIR", tmp_path)
        with patch("v1_api._fetch_options_flow", return_value=None):
            result = v1_api._options_flow_cached("AAPL")
            assert result is None

    def test_oi_always_false_in_flow_response(self, tmp_path, monkeypatch):
        import v1_api
        monkeypatch.setattr(v1_api, "_CACHE_DIR", tmp_path)
        with patch("v1_api._fetch_options_flow", return_value=MOCK_FLOW):
            result = v1_api._options_flow_cached("NVDA")
            assert result["oi_available"] is False


# ---------------------------------------------------------------------------
# Health endpoint test
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Conviction score and momentum tests
# ---------------------------------------------------------------------------

class TestConvictionScore:
    def test_full_signals_high_tier(self):
        import v1_api
        result = v1_api._conviction_score(0.9, True, True, 0.85, momentum_pts=10)
        assert result["tier"] == "high"
        assert result["score"] >= 70

    def test_no_active_driver_reduces_score(self):
        import v1_api
        with_driver = v1_api._conviction_score(0.9, True, True, 0.85)
        without_driver = v1_api._conviction_score(0.9, False, True, 0.85)
        assert with_driver["score"] > without_driver["score"]

    def test_not_in_feed_reduces_score(self):
        import v1_api
        in_feed = v1_api._conviction_score(0.9, True, True, 0.85)
        not_in_feed = v1_api._conviction_score(0.9, True, False, 0.0)
        assert in_feed["score"] > not_in_feed["score"]

    def test_underperforming_drops_tier(self):
        import v1_api
        # Driver active + in feed + strong evidence should be high, but underperforming knocks it down
        at_neutral = v1_api._conviction_score(0.85, True, True, 0.80, momentum_pts=0)
        underperforming = v1_api._conviction_score(0.85, True, True, 0.80, momentum_pts=-15)
        assert underperforming["score"] < at_neutral["score"]

    def test_outperforming_boosts_score(self):
        import v1_api
        neutral = v1_api._conviction_score(0.85, True, True, 0.80, momentum_pts=0)
        outperforming = v1_api._conviction_score(0.85, True, True, 0.80, momentum_pts=15)
        assert outperforming["score"] == neutral["score"] + 15

    def test_score_capped_at_100(self):
        import v1_api
        result = v1_api._conviction_score(1.0, True, True, 1.0, momentum_pts=15)
        assert result["score"] <= 100

    def test_score_floor_at_zero(self):
        import v1_api
        result = v1_api._conviction_score(0.0, False, False, 0.0, momentum_pts=-15)
        assert result["score"] >= 0


class TestMomentumPts:
    def test_strong_outperformer_gets_max_pts(self):
        import v1_api
        data = {"AAPL": 8.0, "SPY": 1.0}
        assert v1_api._momentum_pts("AAPL", data) == 15

    def test_modest_outperformer(self):
        import v1_api
        data = {"AAPL": 3.5, "SPY": 1.0}
        assert v1_api._momentum_pts("AAPL", data) == 10

    def test_slight_outperformer(self):
        import v1_api
        data = {"AAPL": 2.0, "SPY": 1.0}  # relative = +1.0 >= 0.5 → 5 pts
        assert v1_api._momentum_pts("AAPL", data) == 5

    def test_neutral_no_pts(self):
        import v1_api
        data = {"AAPL": 1.0, "SPY": 1.0}
        assert v1_api._momentum_pts("AAPL", data) == 0

    def test_mild_underperformer(self):
        import v1_api
        data = {"AAPL": -1.0, "SPY": 0.5}
        assert v1_api._momentum_pts("AAPL", data) == -8

    def test_strong_underperformer_gets_min_pts(self):
        import v1_api
        data = {"AAPL": -4.0, "SPY": 0.5}
        assert v1_api._momentum_pts("AAPL", data) == -15

    def test_missing_symbol_returns_zero(self):
        import v1_api
        data = {"SPY": 1.0}
        assert v1_api._momentum_pts("AAPL", data) == 0

    def test_missing_spy_returns_zero(self):
        import v1_api
        data = {"AAPL": 5.0}
        assert v1_api._momentum_pts("AAPL", data) == 0

    def test_empty_data_returns_zero(self):
        import v1_api
        assert v1_api._momentum_pts("NVDA", {}) == 0


class TestHealthEndpoint:
    def test_health_returns_200(self, app_dev):
        with app_dev.test_client() as c:
            with patch("v1_api._read_drivers", return_value={
                "mode": "live_market_data",
                "generated_at": "2026-05-30T14:00:00Z",
            }):
                r = c.get("/v1/health")
                assert r.status_code == 200
                body = r.get_json()
                assert body["status"] == "ok"
                assert body["api_version"] == "1"
                assert body["drivers_mode"] == "live_market_data"
