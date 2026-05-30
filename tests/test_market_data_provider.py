"""Tests for market_data_provider.py — generic FMP market data for intelligence cloud."""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import market_data_provider as mdp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _ts(minutes_ago: int = 30) -> str:
    """Return a publishedDate string that is `minutes_ago` minutes in the past."""
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


SAMPLE_GAINERS = [
    {"symbol": "AAPL", "name": "Apple", "price": 200.0, "changesPercentage": 3.5},
    {"symbol": "NVDA", "name": "Nvidia", "price": 600.0, "changesPercentage": 5.2},
    {"symbol": "CHEAP", "name": "Cheap Co", "price": 3.0, "changesPercentage": 90.0},  # under $5 — filtered
]
SAMPLE_LOSERS = [
    {"symbol": "XOM", "name": "Exxon", "price": 110.0, "changesPercentage": -4.1},
]
SAMPLE_NEWS_STOCK = [
    {"title": "Nvidia Reports Record Revenue", "text": "Full text here", "publishedDate": _ts(30),
     "site": "reuters.com", "symbol": "NVDA"},
    {"title": "Apple Launches New Product", "text": "Details...", "publishedDate": _ts(60),
     "site": "techcrunch.com", "symbol": "AAPL"},
]
SAMPLE_NEWS_GENERAL = [
    {"title": "Fed Holds Rates Steady", "text": "Commentary.", "publishedDate": _ts(90),
     "site": "wsj.com", "symbol": None},
    {"title": "Duplicate Article", "text": "dup", "publishedDate": _ts(120),
     "site": "bloomberg.com", "symbol": None},
    {"title": "Duplicate Article", "text": "dup2", "publishedDate": _ts(150),
     "site": "ft.com", "symbol": None},  # duplicate — should be filtered
]
SAMPLE_ETF_QUOTES = [
    {"symbol": "SPY",  "price": 542.0, "change": 5.0},
    {"symbol": "QQQ",  "price": 460.0, "change": 3.0},
    {"symbol": "IWM",  "price": 205.0, "change": -1.0},
    {"symbol": "TLT",  "price": 90.0,  "change": 0.5},
    {"symbol": "GLD",  "price": 220.0, "change": 1.0},
    {"symbol": "USO",  "price": 74.0,  "change": -0.5},
    {"symbol": "UUP",  "price": 27.0,  "change": 0.1},
]
SAMPLE_VIX = [{"price": 18.5, "change": -0.3}]


# ---------------------------------------------------------------------------
# Helper: patch fmp_client._get to return canned data
# ---------------------------------------------------------------------------

def _fmp_side_effect(endpoint: str, params: dict, ttl: float = 0) -> list | None:
    if endpoint == "biggest-gainers":
        return SAMPLE_GAINERS
    if endpoint == "biggest-losers":
        return SAMPLE_LOSERS
    if endpoint == "news/stock-latest":
        return SAMPLE_NEWS_STOCK
    if endpoint == "news/general-latest":
        return SAMPLE_NEWS_GENERAL
    if endpoint == "batch-quote-short":
        return SAMPLE_ETF_QUOTES
    if endpoint == "quote/%5EVIX":
        return SAMPLE_VIX
    return None


# ---------------------------------------------------------------------------
# get_movers tests
# ---------------------------------------------------------------------------

class TestGetMovers:
    def test_returns_gainers_and_losers(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "movers", str(tmp_path / "movers.json"))
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_movers()
        assert "gainers" in result
        assert "losers" in result
        assert result["source"] == "intelligence_api"

    def test_filters_price_below_5(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "movers", str(tmp_path / "movers.json"))
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_movers()
        symbols = [m["symbol"] for m in result["gainers"]]
        assert "CHEAP" not in symbols  # price $3 — filtered

    def test_caps_at_5_results(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "movers", str(tmp_path / "movers.json"))
        big_list = [{"symbol": f"SYM{i}", "name": f"Name{i}", "price": 100.0, "changesPercentage": float(i)} for i in range(20)]
        def mock_get(endpoint: str, params: dict, ttl: float = 0):
            return big_list if "gainers" in endpoint or "losers" in endpoint else None
        with patch.object(mdp.fmp_client, "_get", side_effect=mock_get):
            result = mdp.get_movers()
        assert len(result["gainers"]) <= 5
        assert len(result["losers"]) <= 5

    def test_uses_disk_cache_when_fresh(self, tmp_path, monkeypatch):
        cache_path = str(tmp_path / "movers.json")
        monkeypatch.setitem(mdp._CACHE_PATHS, "movers", cache_path)
        cached = {"gainers": [{"symbol": "CACHED"}], "losers": [], "ts": "cached", "source": "cache"}
        with open(cache_path, "w") as f:
            json.dump(cached, f)
        with patch.object(mdp.fmp_client, "_get") as mock_fmp:
            result = mdp.get_movers()
        mock_fmp.assert_not_called()
        assert result["source"] == "cache"

    def test_ignores_stale_cache(self, tmp_path, monkeypatch):
        cache_path = str(tmp_path / "movers.json")
        monkeypatch.setitem(mdp._CACHE_PATHS, "movers", cache_path)
        monkeypatch.setattr(mdp, "_TTL", 1)
        cached = {"gainers": [], "losers": [], "ts": "old", "source": "cache"}
        with open(cache_path, "w") as f:
            json.dump(cached, f)
        time.sleep(1.1)
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_movers()
        assert result["source"] == "intelligence_api"

    def test_graceful_on_fmp_failure(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "movers", str(tmp_path / "movers.json"))
        with patch.object(mdp.fmp_client, "_get", return_value=None):
            result = mdp.get_movers()
        assert result["gainers"] == []
        assert result["losers"] == []

    def test_pct_string_parsing(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "movers", str(tmp_path / "movers.json"))
        raw = [{"symbol": "XYZ", "name": "XYZ Corp", "price": 50.0, "changesPercentage": "7.5%"}]
        with patch.object(mdp.fmp_client, "_get", return_value=raw):
            result = mdp.get_movers()
        assert result["gainers"][0]["changePct"] == 7.5


# ---------------------------------------------------------------------------
# get_news tests
# ---------------------------------------------------------------------------

class TestGetNews:
    def test_returns_news_list(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "news", str(tmp_path / "news.json"))
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_news()
        assert "news" in result
        assert isinstance(result["news"], list)

    def test_deduplicates_by_title_prefix(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "news", str(tmp_path / "news.json"))
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_news()
        titles = [item["title"] for item in result["news"]]
        assert titles.count("Duplicate Article") == 1

    def test_skips_video_sites(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "news", str(tmp_path / "news.json"))
        with_video = SAMPLE_NEWS_STOCK + [
            {"title": "YouTube Video", "text": "", "publishedDate": "2026-05-27 10:00:00",
             "site": "youtube.com", "symbol": None}
        ]
        def mock_get(endpoint: str, params: dict, ttl: float = 0):
            return with_video if "stock" in endpoint else SAMPLE_NEWS_GENERAL
        with patch.object(mdp.fmp_client, "_get", side_effect=mock_get):
            result = mdp.get_news()
        titles = [item["title"] for item in result["news"]]
        assert "YouTube Video" not in titles

    def test_applies_theme_label(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "news", str(tmp_path / "news.json"))
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_news()
        nvda_item = next((i for i in result["news"] if i.get("symbol") == "NVDA"), None)
        assert nvda_item is not None
        assert nvda_item["themeLabel"] == "AI Infrastructure"

    def test_caps_at_15_items(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "news", str(tmp_path / "news.json"))
        big_news = [{"title": f"Headline {i}", "text": "", "publishedDate": "2026-05-27 10:00:00",
                     "site": "reuters.com", "symbol": None} for i in range(30)]
        with patch.object(mdp.fmp_client, "_get", return_value=big_news):
            result = mdp.get_news()
        assert len(result["news"]) <= 15

    def test_graceful_on_fmp_failure(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "news", str(tmp_path / "news.json"))
        with patch.object(mdp.fmp_client, "_get", return_value=None):
            result = mdp.get_news()
        assert result["news"] == []


# ---------------------------------------------------------------------------
# get_tape tests
# ---------------------------------------------------------------------------

class TestGetTape:
    def test_returns_tape_with_all_symbols(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "tape", str(tmp_path / "tape.json"))
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_tape()
        syms = {e["sym"] for e in result["tape"]}
        assert {"SPY", "QQQ", "IWM", "TLT", "GLD", "USO", "UUP", "VIX"} == syms

    def test_vix_included(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "tape", str(tmp_path / "tape.json"))
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_tape()
        vix = next(e for e in result["tape"] if e["sym"] == "VIX")
        assert vix["level"] == 18.5
        assert vix["type"] == "vol"

    def test_pct_computed_correctly(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "tape", str(tmp_path / "tape.json"))
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_tape()
        spy = next(e for e in result["tape"] if e["sym"] == "SPY")
        # SPY price=542, change=5 → prev=537 → pct = 5/537*100 ≈ 0.93%
        assert spy["changePct"] is not None
        assert abs(spy["changePct"] - 0.93) < 0.01

    def test_null_levels_on_fmp_failure(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "tape", str(tmp_path / "tape.json"))
        def mock_get(endpoint: str, params: dict, ttl: float = 0):
            if "batch-quote" in endpoint:
                return None  # FMP failure
            return SAMPLE_VIX if "VIX" in endpoint else None
        with patch.object(mdp.fmp_client, "_get", side_effect=mock_get):
            result = mdp.get_tape()
        spy = next(e for e in result["tape"] if e["sym"] == "SPY")
        assert spy["changePct"] is None
        assert spy["level"] is None

    def test_tape_type_labels(self, tmp_path, monkeypatch):
        monkeypatch.setitem(mdp._CACHE_PATHS, "tape", str(tmp_path / "tape.json"))
        with patch.object(mdp.fmp_client, "_get", side_effect=_fmp_side_effect):
            result = mdp.get_tape()
        tape_map = {e["sym"]: e["type"] for e in result["tape"]}
        assert tape_map["SPY"]  == "equity"
        assert tape_map["TLT"]  == "rates"
        assert tape_map["GLD"]  == "safe_haven"
        assert tape_map["USO"]  == "commodity"
        assert tape_map["UUP"]  == "dollar"
        assert tape_map["VIX"]  == "vol"


# ---------------------------------------------------------------------------
# _safe_pct tests
# ---------------------------------------------------------------------------

class TestSafePct:
    def test_standard_calc(self):
        assert mdp._safe_pct(110.0, 10.0) == pytest.approx(10.0)

    def test_zero_prev_returns_none(self):
        assert mdp._safe_pct(5.0, 5.0) is None  # prev = 0

    def test_none_inputs(self):
        assert mdp._safe_pct(None, 1.0) is None
        assert mdp._safe_pct(100.0, None) is None
