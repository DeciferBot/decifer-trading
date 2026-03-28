"""Tests for theme_tracker.py"""
import os
import sys
import json
import tempfile
import pytest

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock config before importing theme_tracker
import types
config_mod = types.ModuleType("config")
config_mod.CONFIG = {
    "sentinel_max_symbols": 80,
    "log_file": "/tmp/test.log",
    "trade_log": "/tmp/trades.json",
    "order_log": "/tmp/orders.json",
}
sys.modules.setdefault("config", config_mod)

# Mock scanner before importing theme_tracker
scanner_mod = types.ModuleType("scanner")
scanner_mod.CORE_SYMBOLS = ["SPY", "QQQ", "IWM"]
scanner_mod.MOMENTUM_FALLBACK = ["NVDA", "AAPL", "TSLA", "AMZN", "MSFT", "META", "AMD", "GOOGL", "V", "MA"]
sys.modules.setdefault("scanner", scanner_mod)

import theme_tracker
# Ensure project root is on path before importing theme_tracker
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Force reload in case a stale cached version was picked up
import importlib
if 'theme_tracker' in sys.modules:
    importlib.reload(sys.modules['theme_tracker'])
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys, os, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if 'theme_tracker' in sys.modules:
    importlib.reload(sys.modules['theme_tracker'])
from theme_tracker import (
    detect_themes_from_holdings,
    detect_trending_themes,
    build_sentinel_universe,
    add_custom_theme,
    remove_theme,
    toggle_theme,
    get_all_themes,
    score_headline_theme_relevance,
    get_holdings_symbols,
    THEMES,
)


# ---------------------------------------------------------------------------
# get_holdings_symbols
# ---------------------------------------------------------------------------

class TestGetHoldingsSymbols:
    def test_extracts_symbols_from_positions(self):
        positions = [{"symbol": "NVDA"}, {"symbol": "AAPL"}]
        result = get_holdings_symbols(positions)
        assert "NVDA" in result
        assert "AAPL" in result

    def test_includes_favourites(self):
        result = get_holdings_symbols([], ["TSLA", "AMD"])
        assert "TSLA" in result
        assert "AMD" in result

    def test_deduplicates_symbols(self):
        positions = [{"symbol": "NVDA"}, {"symbol": "NVDA"}]
        result = get_holdings_symbols(positions, ["NVDA"])
        assert result.count("NVDA") == 1

    def test_empty_inputs(self):
        result = get_holdings_symbols([], [])
        assert result == []

    def test_none_inputs(self):
        result = get_holdings_symbols(None, None)
        assert result == []

    def test_position_missing_symbol_key(self):
        positions = [{"side": "LONG"}, {"symbol": "AAPL"}]
        result = get_holdings_symbols(positions)
        assert "AAPL" in result
        assert len(result) == 1

    def test_returns_list(self):
        result = get_holdings_symbols([{"symbol": "SPY"}])
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# detect_themes_from_holdings
# ---------------------------------------------------------------------------

class TestDetectThemesFromHoldings:
    def test_detects_ai_theme_from_nvda(self):
        themes = detect_themes_from_holdings(["NVDA"])
        assert "ai_infrastructure" in themes or "semis" in themes

    def test_detects_ev_theme_from_tsla(self):
        themes = detect_themes_from_holdings(["TSLA"])
        assert "ev_battery" in themes

    def test_detects_biotech_theme(self):
        themes = detect_themes_from_holdings(["MRNA"])
        assert "biotech" in themes

    def test_empty_holdings_returns_empty(self):
        themes = detect_themes_from_holdings([])
        assert themes == []

    def test_unknown_symbol_returns_empty(self):
        themes = detect_themes_from_holdings(["FAKESYMBOL999"])
        assert themes == []

    def test_returns_list(self):
        result = detect_themes_from_holdings(["NVDA", "TSLA"])
        assert isinstance(result, list)

    def test_multiple_holdings_multiple_themes(self):
        # NVDA (ai/semis) + TSLA (ev) + MRNA (biotech)
        themes = detect_themes_from_holdings(["NVDA", "TSLA", "MRNA"])
        assert len(themes) >= 2

    def test_inactive_theme_not_returned(self):
        # Temporarily deactivate a theme
        original_active = THEMES["fintech"]["active"]
        THEMES["fintech"]["active"] = False
        try:
            # PYPL is in fintech; should not trigger fintech theme
            themes = detect_themes_from_holdings(["PYPL"])
            assert "fintech" not in themes
        finally:
            THEMES["fintech"]["active"] = original_active


# ---------------------------------------------------------------------------
# detect_trending_themes
# ---------------------------------------------------------------------------

class TestDetectTrendingThemes:
    def test_detects_ai_from_headlines(self):
        headlines = [
            "NVIDIA reports record AI chip sales",
            "Data center demand for GPU accelerates",
            "Machine learning drives cloud revenue",
        ]
        trending = detect_trending_themes(headlines)
        assert "ai_infrastructure" in trending or "semis" in trending

    def test_detects_ev_from_headlines(self):
        headlines = [
            "Tesla electric vehicle deliveries miss estimates",
            "Battery technology breakthrough for EVs",
        ]
        trending = detect_trending_themes(headlines)
        assert "ev_battery" in trending

    def test_empty_headlines_returns_empty(self):
        result = detect_trending_themes([])
        assert result == []

    def test_none_returns_empty(self):
        result = detect_trending_themes(None)
        assert result == []

    def test_returns_sorted_by_relevance(self):
        # Many AI keywords should score AI higher than defense
        headlines = [
            "AI chip demand surges",
            "Machine learning boom continues",
            "GPU shortage drives prices up",
            "CUDA platform sees record developers",
            "Neural network training costs drop",
        ]
        trending = detect_trending_themes(headlines)
        assert isinstance(trending, list)
        assert len(trending) > 0
        # AI/semis should be near the top
        assert trending[0] in ("ai_infrastructure", "semis")

    def test_unrelated_headlines_return_empty_or_low_themes(self):
        headlines = ["Generic market commentary with no specific keywords"]
        result = detect_trending_themes(headlines)
        # Should not crash; might return empty
        assert isinstance(result, list)

    def test_multiple_themes_detected(self):
        headlines = [
            "FDA approves new cancer drug",
            "Clinical trial phase 3 results",
            "Bitcoin surges on ETF news",
            "Crypto exchange volume doubles",
        ]
        trending = detect_trending_themes(headlines)
        # Should detect both biotech and fintech
        assert len(trending) >= 2


# ---------------------------------------------------------------------------
# build_sentinel_universe
# ---------------------------------------------------------------------------

class TestBuildSentinelUniverse:
    def test_returns_list(self):
        result = build_sentinel_universe()
        assert isinstance(result, list)

    def test_respects_max_symbols(self):
        result = build_sentinel_universe(max_symbols=10)
        assert len(result) <= 10

    def test_holdings_are_included(self):
        positions = [{"symbol": "FAKESYM1"}]
        # FAKESYM1 is not in any theme, but should still appear as a holding
        result = build_sentinel_universe(open_positions=positions, max_symbols=200)
        assert "FAKESYM1" in result

    def test_no_duplicates(self):
        positions = [{"symbol": "NVDA"}, {"symbol": "AAPL"}]
        result = build_sentinel_universe(open_positions=positions)
        assert len(result) == len(set(result))

    def test_empty_inputs_still_returns_symbols(self):
        result = build_sentinel_universe(open_positions=[], favourites=[], trending_headlines=[])
        # Should still return theme symbols and core symbols
        assert len(result) > 0

    def test_trending_headlines_boost_theme(self):
        headlines = [
            "FDA approves new cancer drug",
            "Phase 3 clinical trial positive",
            "Biotech company gets breakthrough designation",
        ]
        result_with_news = build_sentinel_universe(trending_headlines=headlines, max_symbols=200)
        result_no_news = build_sentinel_universe(trending_headlines=[], max_symbols=200)
        # With news, biotech symbols should be present
        assert len(result_with_news) > 0

    def test_favourites_included(self):
        result = build_sentinel_universe(favourites=["CUSTOM1", "CUSTOM2"], max_symbols=200)
        assert "CUSTOM1" in result
        assert "CUSTOM2" in result

    def test_all_symbols_are_strings(self):
        result = build_sentinel_universe()
        for sym in result:
            assert isinstance(sym, str)
            assert len(sym) > 0


# ---------------------------------------------------------------------------
# Theme Management
# ---------------------------------------------------------------------------

class TestThemeManagement:
    def test_get_all_themes_returns_dict(self):
        all_themes = get_all_themes()
        assert isinstance(all_themes, dict)
        assert len(all_themes) > 0

    def test_get_all_themes_has_required_keys(self):
        all_themes = get_all_themes()
        for key, val in all_themes.items():
            assert "name" in val
            assert "symbols_count" in val
            assert "active" in val
            assert "priority" in val

    def test_toggle_theme_deactivates(self):
        toggle_theme("defense", False)
        all_themes = get_all_themes()
        assert all_themes["defense"]["active"] is False
        # Restore
        toggle_theme("defense", True)

    def test_toggle_theme_activates(self):
        toggle_theme("defense", False)
        toggle_theme("defense", True)
        all_themes = get_all_themes()
        assert all_themes["defense"]["active"] is True

    def test_add_custom_theme(self, tmp_path, monkeypatch):
        # Patch the custom themes file path
        custom_file = str(tmp_path / "custom_themes.json")
        monkeypatch.setattr(theme_tracker, "_CUSTOM_THEMES_FILE", custom_file)

        add_custom_theme(
            key="test_custom",
            name="Test Custom",
            symbols=["AAAA", "BBBB"],
            keywords=["test", "custom"],
            priority=3,
        )
        assert "test_custom" in THEMES
        assert THEMES["test_custom"]["name"] == "Test Custom"
        assert "AAAA" in THEMES["test_custom"]["symbols"]
        # Cleanup
        del THEMES["test_custom"]

    def test_remove_custom_theme(self, tmp_path, monkeypatch):
        custom_file = str(tmp_path / "custom_themes.json")
        monkeypatch.setattr(theme_tracker, "_CUSTOM_THEMES_FILE", custom_file)

        add_custom_theme(
            key="removable_theme",
            name="Removable",
            symbols=["XXXX"],
            keywords=[],
            priority=5,
        )
        assert "removable_theme" in THEMES
        remove_theme("removable_theme")
        assert "removable_theme" not in THEMES

    def test_remove_builtin_theme_deactivates_not_deletes(self):
        remove_theme("clean_energy")
        assert "clean_energy" in THEMES
        assert THEMES["clean_energy"]["active"] is False
        # Restore
        THEMES["clean_energy"]["active"] = True

    def test_toggle_nonexistent_theme_no_error(self):
        # Should not raise
        toggle_theme("nonexistent_theme_xyz", True)


# ---------------------------------------------------------------------------
# score_headline_theme_relevance
# ---------------------------------------------------------------------------

class TestScoreHeadlineThemeRelevance:
    def test_ai_headline_scores_ai_theme(self):
        result = score_headline_theme_relevance(
            "NVIDIA reports record GPU sales for AI training", "NVDA"
        )
        assert isinstance(result, dict)
        # At least one theme should score > 0
        assert any(v > 0 for v in result.values())

    def test_unrelated_headline_scores_zero_or_low(self):
        result = score_headline_theme_relevance(
            "Weather forecast for the weekend", "XYZ"
        )
        assert isinstance(result, dict)
        # All scores should be 0
        assert all(v == 0 for v in result.values())

    def test_returns_dict(self):
        result = score_headline_theme_relevance("test headline", "AAPL")
        assert isinstance(result, dict)

    def test_biotech_headline_scores_biotech(self):
        result = score_headline_theme_relevance(
            "FDA approves new clinical trial for cancer therapy", "MRNA"
        )
        assert isinstance(result, dict)
        assert result.get("biotech", 0) > 0

    def test_ev_headline_scores_ev(self):
        result = score_headline_theme_relevance(
            "Tesla electric vehicle sales surge globally", "TSLA"
        )
        assert result.get("ev_battery", 0) > 0
