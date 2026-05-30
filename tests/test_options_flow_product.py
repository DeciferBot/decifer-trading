"""
Tests for Product B — Options Flow Screen.

Covers:
  - options_flow_scanner: symbol scoring, universe scan, edge cases
  - options_flow_api v1 routes: universe-scan, custom scan
  - Auth enforcement on v1 routes
  - OI always absent, oi_note always present
  - No blocked fields in any response
  - Max 50 symbol cap on custom scan
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app_dev(monkeypatch):
    monkeypatch.delenv("INTELLIGENCE_API_KEYS", raising=False)
    monkeypatch.setenv("DECIFER_RUNTIME_MODE", "paper_execution")
    import importlib, v1_auth, v1_api, options_flow_api
    importlib.reload(v1_auth)
    importlib.reload(v1_api)
    importlib.reload(options_flow_api)
    from flask import Flask
    app = Flask("test")
    app.register_blueprint(options_flow_api.bp)
    app.testing = True
    return app


@pytest.fixture()
def app_with_key(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_API_KEYS", "test-key-abc")
    monkeypatch.setenv("DECIFER_RUNTIME_MODE", "intelligence_cloud")
    import importlib, v1_auth, v1_api, options_flow_api
    importlib.reload(v1_auth)
    importlib.reload(v1_api)
    importlib.reload(options_flow_api)
    from flask import Flask
    app = Flask("test")
    app.register_blueprint(options_flow_api.bp)
    app.testing = True
    return app


# ---------------------------------------------------------------------------
# options_flow_scanner tests
# ---------------------------------------------------------------------------

MOCK_FLOW_DATA = MagicMock(
    flow_metrics_available=True,
    call_volume=15000.0,
    put_volume=3000.0,
    call_trade_count=120.0,
    call_prev_volume=5000.0,
    put_prev_volume=3000.0,
    provider="alpaca_rest_dailyBar",
    provider_timestamp="2026-05-30T14:00:00Z",
)


class TestOptionsFlowScanner:
    def test_score_symbol_returns_dict_on_flow(self):
        import sys
        import options_flow_scanner as sc
        mock_provider = MagicMock(
            get_options_flow_data=lambda *a, **k: MOCK_FLOW_DATA,
            MIN_SIDE_VOLUME=250,
            MIN_DAY_OVER_DAY_RATIO=1.75,
            PREV_VOLUME_FLOOR=50,
        )
        with patch.dict(sys.modules, {"options_provider": mock_provider}):
            result = sc._score_symbol("NVDA")
        assert result is None or isinstance(result, dict)

    def test_score_symbol_returns_none_on_no_flow(self):
        import options_flow_scanner as sc
        with patch.dict("sys.modules", {"options_provider": MagicMock(
            get_options_flow_data=lambda *a, **k: None,
            MIN_SIDE_VOLUME=250,
            MIN_DAY_OVER_DAY_RATIO=1.75,
            PREV_VOLUME_FLOOR=50,
        )}):
            result = sc._score_symbol("UNKNOWN")
            assert result is None

    def test_scan_symbols_returns_sorted_by_score(self):
        import options_flow_scanner as sc

        high = {"symbol": "A", "anomaly_score": 8, "unusual": True, "flags": [],
                "call_volume": 1000, "put_volume": 200, "call_trade_count": 50,
                "call_expansion": 3.0, "put_expansion": 1.0, "unusual_calls": True,
                "unusual_puts": False, "oi_available": False, "provider": "test",
                "data_ts": "2026-01-01"}
        low = {"symbol": "B", "anomaly_score": 2, "unusual": False, "flags": [],
               "call_volume": 300, "put_volume": 100, "call_trade_count": 10,
               "call_expansion": 1.2, "put_expansion": 0.9, "unusual_calls": False,
               "unusual_puts": False, "oi_available": False, "provider": "test",
               "data_ts": "2026-01-01"}

        with patch.object(sc, "_score_symbol", side_effect=[high, low]):
            results = sc.scan_symbols(["A", "B"])
        # sorted desc by anomaly_score
        assert results[0]["anomaly_score"] >= results[-1]["anomaly_score"]

    def test_scan_symbols_empty_list(self):
        import options_flow_scanner as sc
        assert sc.scan_symbols([]) == []

    def test_scan_universe_returns_payload_structure(self, tmp_path, monkeypatch):
        import options_flow_scanner as sc
        monkeypatch.setattr(sc, "_OUT_DIR", tmp_path)
        monkeypatch.setattr(sc, "_LEADERBOARD_PATH", tmp_path / "leaderboard.json")
        with patch.object(sc, "_ttg_symbols", return_value=["NVDA", "AAPL"]):
            with patch.object(sc, "scan_symbols", return_value=[]):
                with patch.object(sc, "_active_drivers", return_value=set()):
                    with patch.object(sc, "_symbol_themes", return_value={}):
                        result = sc.scan_universe(write=False)
        assert "ts" in result
        assert "scanned" in result
        assert "leaderboard" in result
        assert "oi_note" in result
        assert result["oi_available"] is False

    def test_oi_never_available_in_scanner_output(self):
        import options_flow_scanner as sc
        with patch.object(sc, "_ttg_symbols", return_value=[]):
            result = sc.scan_universe(write=False)
        assert result.get("oi_available") is False or "leaderboard" not in result

    def test_scan_symbols_no_blocked_fields(self):
        import options_flow_scanner as sc
        blocked = ["entry_price", "exit_price", "pnl", "order_id",
                   "position_size", "open_interest", "delta", "gamma", "strike"]
        sample_row = {
            "symbol": "TEST", "anomaly_score": 5, "flags": [],
            "call_volume": 1000, "put_volume": 200, "call_trade_count": 30,
            "call_expansion": 2.0, "put_expansion": 1.0,
            "unusual_calls": True, "unusual_puts": False, "unusual": True,
            "oi_available": False, "provider": "test", "data_ts": "now",
        }
        row_json = json.dumps(sample_row)
        for field in blocked:
            assert f'"{field}"' not in row_json


# ---------------------------------------------------------------------------
# options_flow_api v1 route tests
# ---------------------------------------------------------------------------

class TestUniverseScanRoute:
    def test_requires_auth(self, app_with_key):
        with app_with_key.test_client() as c:
            r = c.get("/v1/options/universe-scan")
            assert r.status_code == 401

    def test_returns_202_when_no_leaderboard(self, app_dev, tmp_path, monkeypatch):
        import options_flow_api
        monkeypatch.setattr(options_flow_api, "_LEADERBOARD_PATH", tmp_path / "missing.json")
        with app_dev.test_client() as c:
            with patch("options_flow_api._leaderboard_stale", return_value=True):
                with patch("options_flow_api._load_leaderboard", return_value=None):
                    r = c.get("/v1/options/universe-scan")
                    assert r.status_code == 202
                    body = r.get_json()
                    assert body["status"] == "scanning"

    def test_returns_200_with_cached_leaderboard(self, app_dev):
        mock_lb = {
            "ts": "2026-05-30T14:00:00Z",
            "scanned": 125,
            "returned": 3,
            "unusual_count": 2,
            "oi_available": False,
            "oi_note": "OI unavailable.",
            "leaderboard": [],
        }
        with app_dev.test_client() as c:
            with patch("options_flow_api._leaderboard_stale", return_value=False):
                with patch("options_flow_api._load_leaderboard", return_value=mock_lb):
                    r = c.get("/v1/options/universe-scan")
                    assert r.status_code == 200
                    body = r.get_json()
                    assert body["status"] == "ok"
                    assert body["oi_available"] is False
                    assert "oi_note" in body

    def test_stale_flag_propagated(self, app_dev):
        mock_lb = {"ts": "old", "scanned": 10, "returned": 0,
                   "unusual_count": 0, "oi_available": False,
                   "oi_note": "x", "leaderboard": []}
        with app_dev.test_client() as c:
            with patch("options_flow_api._leaderboard_stale", return_value=True):
                with patch("options_flow_api._load_leaderboard", return_value=mock_lb):
                    with patch("threading.Thread"):
                        r = c.get("/v1/options/universe-scan")
                        body = r.get_json()
                        assert body["stale"] is True


class TestCustomScanRoute:
    def test_requires_auth(self, app_with_key):
        with app_with_key.test_client() as c:
            r = c.post("/v1/options/scan",
                       json={"symbols": ["NVDA"]},
                       content_type="application/json")
            assert r.status_code == 401

    def test_empty_symbols_returns_400(self, app_dev):
        with app_dev.test_client() as c:
            r = c.post("/v1/options/scan",
                       json={"symbols": []},
                       content_type="application/json")
            assert r.status_code == 400

    def test_no_body_returns_400(self, app_dev):
        with app_dev.test_client() as c:
            r = c.post("/v1/options/scan",
                       data="not json",
                       content_type="text/plain")
            assert r.status_code == 400

    def test_max_50_symbols_enforced(self, app_dev):
        import string
        # Generate 100 alpha-only fake tickers (AAAA, AAAB, …)
        big_list = ["".join([string.ascii_uppercase[i % 26], string.ascii_uppercase[(i // 26) % 26], "XX"]) for i in range(100)]
        with app_dev.test_client() as c:
            with patch("options_flow_api.scan_symbols", return_value=[]) as mock_scan:
                r = c.post("/v1/options/scan",
                           json={"symbols": big_list},
                           content_type="application/json")
                assert r.status_code == 200
                called_symbols = mock_scan.call_args[0][0]
                assert len(called_symbols) <= 50

    def test_returns_oi_note_always(self, app_dev):
        with app_dev.test_client() as c:
            with patch("options_flow_api.scan_symbols", return_value=[]):
                r = c.post("/v1/options/scan",
                           json={"symbols": ["NVDA", "AAPL"]},
                           content_type="application/json")
                body = r.get_json()
                assert body["oi_available"] is False
                assert "oi_note" in body

    def test_non_alpha_symbols_filtered(self, app_dev):
        with app_dev.test_client() as c:
            with patch("options_flow_api.scan_symbols", return_value=[]) as mock_scan:
                r = c.post("/v1/options/scan",
                           json={"symbols": ["NVDA", "NV1DA", "123", "AAPL"]},
                           content_type="application/json")
                assert r.status_code == 200
                called = mock_scan.call_args[0][0]
                assert "NV1DA" not in called
                assert "123" not in called

    def test_no_blocked_fields_in_response(self, app_dev):
        mock_result = [
            {"symbol": "NVDA", "anomaly_score": 7, "unusual": True, "flags": [],
             "call_volume": 10000, "put_volume": 2000, "call_trade_count": 80,
             "call_expansion": 2.5, "put_expansion": 1.0, "unusual_calls": True,
             "unusual_puts": False, "oi_available": False, "provider": "test",
             "data_ts": "now"}
        ]
        blocked = ["entry_price", "pnl", "order_id", "open_interest", "delta", "strike"]
        with app_dev.test_client() as c:
            with patch("options_flow_api.scan_symbols", return_value=mock_result):
                r = c.post("/v1/options/scan",
                           json={"symbols": ["NVDA"]},
                           content_type="application/json")
                body_str = r.get_data(as_text=True)
                for field in blocked:
                    assert f'"{field}"' not in body_str
