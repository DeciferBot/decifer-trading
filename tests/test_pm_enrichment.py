"""
test_pm_enrichment.py — Unit tests for pm_enrichment.enrich_review_positions.

Coverage:
- enriched fields populated when FMP/Alpaca return data
- individual layer failure degrades gracefully (field → None, position still present)
- Alpaca failure → price structure fields None, position intact
- FMP failure → analyst/fundamentals fields None, position intact
- thesis_intact read from universe handoff when file exists
- theme concentration: peers counted correctly, % computed from portfolio value
- theme concentration: no peers when different drivers
- empty positions list returns immediately
- all_open_positions=None → theme_peers=[], theme_concentration_pct=None
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ["ib_async", "ib_insync", "anthropic", "yfinance", "praw", "feedparser",
             "tvDatafeed", "requests_html"]:
    sys.modules.setdefault(_mod, MagicMock())

import pm_enrichment as pe


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _position(sym: str, qty: float = 100, entry: float = 100.0, current: float = 105.0) -> dict:
    return {
        "symbol":        sym,
        "trade_type":    "SWING",
        "direction":     "long",
        "qty":           qty,
        "entry":         entry,
        "current":       current,
        "current_price": current,
        "pnl_pct":       (current - entry) / entry,
    }


def _universe(candidates: list[dict], tmp_path) -> str:
    path = str(tmp_path / "universe.json")
    with open(path, "w") as f:
        json.dump({"candidates": candidates}, f)
    return path


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

def test_empty_positions_returns_immediately():
    result = pe.enrich_review_positions([])
    assert result == []


# ---------------------------------------------------------------------------
# Layer 1 — analyst enrichment
# ---------------------------------------------------------------------------

def test_analyst_fields_populated_when_fmp_returns_data(tmp_path):
    pos = _position("AAPL", current=180.0)
    universe_path = _universe([], tmp_path)

    consensus_data = {"symbol": "AAPL", "consensus": "BUY", "last_updated": "2026-05-01"}
    pt_data = {"symbol": "AAPL", "pt_consensus": 220.0, "latest_pt": 220.0, "pt_upside_pct": None}
    grades_data = {"strong_buy": 15, "buy": 10, "hold": 5, "sell": 2, "strong_sell": 0, "total_analysts": 32}

    with patch("fmp_client.warm_fundamentals_cache"), \
         patch("fmp_client.get_analyst_consensus", return_value=consensus_data), \
         patch("fmp_client.get_price_target",      return_value=pt_data), \
         patch("fmp_client.get_analyst_grades",     return_value=grades_data), \
         patch("pm_enrichment._enrich_price_structure", return_value={"week52_high": None, "week52_high_distance_pct": None, "stock_above_200d": None, "thesis_intact": None}), \
         patch("pm_enrichment._enrich_fundamentals",   return_value={"pe_ratio": None, "is_profitable": None, "revenue_growth_yoy": None, "revenue_decelerating": None, "fcf_yield": None}):
        result = pe.enrich_review_positions([pos], universe_path=universe_path)

    assert result[0]["analyst_consensus"] == "BUY"
    assert result[0]["analyst_pt"] == 220.0
    assert result[0]["analyst_upside_pct"] == pytest.approx(22.2, abs=0.5)
    assert result[0]["analyst_buy_count"] == 25   # 15 strong_buy + 10 buy
    assert result[0]["analyst_sell_count"] == 2


def test_analyst_fields_none_when_fmp_fails(tmp_path):
    pos = _position("XYZ")
    universe_path = _universe([], tmp_path)

    with patch("fmp_client.warm_fundamentals_cache"), \
         patch("fmp_client.get_analyst_consensus", return_value=None), \
         patch("fmp_client.get_price_target",      return_value=None), \
         patch("fmp_client.get_analyst_grades",     return_value=None), \
         patch("pm_enrichment._enrich_price_structure", return_value={"week52_high": None, "week52_high_distance_pct": None, "stock_above_200d": None, "thesis_intact": None}), \
         patch("pm_enrichment._enrich_fundamentals",   return_value={"pe_ratio": None, "is_profitable": None, "revenue_growth_yoy": None, "revenue_decelerating": None, "fcf_yield": None}):
        result = pe.enrich_review_positions([pos], universe_path=universe_path)

    assert result[0]["analyst_consensus"] is None
    assert result[0]["analyst_pt"] is None
    assert result[0]["analyst_upside_pct"] is None
    # Position still present
    assert result[0]["symbol"] == "XYZ"


# ---------------------------------------------------------------------------
# Layer 2 — price structure
# ---------------------------------------------------------------------------

def test_price_structure_fields_populated(tmp_path):
    pos = _position("NVDA", current=800.0)
    universe_path = _universe([{"symbol": "NVDA", "thesis_intact": False, "macro_rules_fired": []}], tmp_path)

    # thesis_intact should be read from universe
    price_struct = {"week52_high": 950.0, "week52_high_distance_pct": -15.8, "stock_above_200d": True, "thesis_intact": False}
    with patch("fmp_client.warm_fundamentals_cache"), \
         patch("pm_enrichment._enrich_analyst",         return_value={"analyst_consensus": None, "analyst_pt": None, "analyst_upside_pct": None, "analyst_buy_count": None, "analyst_sell_count": None, "analyst_total": None}), \
         patch("pm_enrichment._enrich_price_structure", return_value=price_struct), \
         patch("pm_enrichment._enrich_fundamentals",    return_value={"pe_ratio": None, "is_profitable": None, "revenue_growth_yoy": None, "revenue_decelerating": None, "fcf_yield": None}):
        result = pe.enrich_review_positions([pos], universe_path=universe_path)

    assert result[0]["week52_high"] == 950.0
    assert result[0]["week52_high_distance_pct"] == pytest.approx(-15.8, abs=0.1)
    assert result[0]["stock_above_200d"] is True
    assert result[0]["thesis_intact"] is False


def test_thesis_intact_from_universe_handoff(tmp_path):
    universe_path = _universe(
        [{"symbol": "VRT", "thesis_intact": False, "macro_rules_fired": ["ai_capex_growth_to_data_centre_power"]}],
        tmp_path,
    )
    thesis_map = pe._load_universe_thesis_map(universe_path)
    assert thesis_map["VRT"] is False


def test_thesis_intact_missing_returns_none(tmp_path):
    universe_path = _universe([], tmp_path)
    thesis_map = pe._load_universe_thesis_map(universe_path)
    assert thesis_map.get("VRT") is None


def test_thesis_intact_missing_file_returns_empty():
    thesis_map = pe._load_universe_thesis_map("/nonexistent/path/universe.json")
    assert thesis_map == {}


# ---------------------------------------------------------------------------
# Layer 3 — fundamentals
# ---------------------------------------------------------------------------

def test_fundamentals_populated_from_fmp(tmp_path):
    pos = _position("MSFT")
    universe_path = _universe([], tmp_path)

    metrics = {"pe_ratio": 32.5, "fcf_yield": 2.8, "net_margin": 35.0, "symbol": "MSFT"}
    rev     = {"revenue_growth_yoy": 17.0, "revenue_deceleration": False}

    with patch("fmp_client.warm_fundamentals_cache"), \
         patch("pm_enrichment._enrich_analyst",         return_value={"analyst_consensus": None, "analyst_pt": None, "analyst_upside_pct": None, "analyst_buy_count": None, "analyst_sell_count": None, "analyst_total": None}), \
         patch("pm_enrichment._enrich_price_structure", return_value={"week52_high": None, "week52_high_distance_pct": None, "stock_above_200d": None, "thesis_intact": None}), \
         patch("fmp_client.get_key_metrics_ttm",        return_value=metrics), \
         patch("fmp_client.get_revenue_growth",         return_value=rev):
        result = pe.enrich_review_positions([pos], universe_path=universe_path)

    assert result[0]["pe_ratio"] == 32.5
    assert result[0]["is_profitable"] is True   # net_margin > 0
    assert result[0]["revenue_growth_yoy"] == 17.0
    assert result[0]["revenue_decelerating"] is False
    assert result[0]["fcf_yield"] == 2.8


def test_fundamentals_none_on_fmp_failure(tmp_path):
    pos = _position("ABC")
    universe_path = _universe([], tmp_path)

    with patch("fmp_client.warm_fundamentals_cache"), \
         patch("pm_enrichment._enrich_analyst",         return_value={"analyst_consensus": None, "analyst_pt": None, "analyst_upside_pct": None, "analyst_buy_count": None, "analyst_sell_count": None, "analyst_total": None}), \
         patch("pm_enrichment._enrich_price_structure", return_value={"week52_high": None, "week52_high_distance_pct": None, "stock_above_200d": None, "thesis_intact": None}), \
         patch("fmp_client.get_key_metrics_ttm",        return_value=None), \
         patch("fmp_client.get_revenue_growth",         return_value=None):
        result = pe.enrich_review_positions([pos], universe_path=universe_path)

    assert result[0]["pe_ratio"] is None
    assert result[0]["is_profitable"] is None
    assert result[0]["symbol"] == "ABC"  # position still there


# ---------------------------------------------------------------------------
# Layer 4 — theme concentration
# ---------------------------------------------------------------------------

def test_theme_concentration_counts_peers(tmp_path):
    universe_candidates = [
        {"symbol": "VRT",  "macro_rules_fired": ["ai_capex_growth_to_data_centre_power"]},
        {"symbol": "ETN",  "macro_rules_fired": ["ai_capex_growth_to_electrical_infrastructure"]},
        {"symbol": "PWR",  "macro_rules_fired": ["ai_capex_growth_to_electrical_infrastructure"]},
        {"symbol": "AVGO", "macro_rules_fired": ["ai_capex_growth_to_semiconductors"]},
    ]
    universe_path = _universe(universe_candidates, tmp_path)

    pos_vrt  = _position("VRT",  qty=100, current=100.0)
    pos_etn  = _position("ETN",  qty=50,  current=200.0)
    pos_pwr  = _position("PWR",  qty=200, current=50.0)
    pos_avgo = _position("AVGO", qty=10,  current=500.0)

    all_positions = [pos_vrt, pos_etn, pos_pwr, pos_avgo]
    portfolio_value = sum(p["qty"] * p["current"] for p in all_positions)  # 10000+10000+10000+5000 = 35000

    result = pe._enrich_theme_concentration(pos_vrt, all_positions, universe_candidates, portfolio_value)

    assert set(result["theme_peers"]) == {"ETN", "PWR", "AVGO"}
    # VRT notional = 10000; ETN = 10000; PWR = 10000; AVGO = 5000 → total 35000 / 35000 = 100%
    assert result["theme_concentration_pct"] == pytest.approx(100.0, abs=0.5)


def test_theme_concentration_no_peers_different_driver(tmp_path):
    universe_candidates = [
        {"symbol": "VRT", "macro_rules_fired": ["ai_capex_growth_to_data_centre_power"]},
        {"symbol": "XOM", "macro_rules_fired": ["oil_supply_shock_to_integrated_oil"]},
    ]
    pos_vrt = _position("VRT")
    pos_xom = _position("XOM")
    result  = pe._enrich_theme_concentration(pos_vrt, [pos_vrt, pos_xom], universe_candidates, 20000.0)
    assert result["theme_peers"] == []


def test_theme_concentration_no_peers_returns_own_concentration(tmp_path):
    universe_candidates = [
        {"symbol": "VRT", "macro_rules_fired": ["ai_capex_growth_to_data_centre_power"]},
    ]
    pos_vrt = _position("VRT", qty=100, current=100.0)
    result  = pe._enrich_theme_concentration(pos_vrt, [pos_vrt], universe_candidates, 10000.0)
    assert result["theme_peers"] == []
    assert result["theme_concentration_pct"] == pytest.approx(100.0, abs=0.1)


def test_theme_concentration_zero_portfolio_value():
    result = pe._enrich_theme_concentration(_position("VRT"), [_position("VRT")], [], 0.0)
    assert result["theme_concentration_pct"] is None


def test_theme_concentration_no_open_positions():
    result = pe.enrich_review_positions(
        [_position("VRT")],
        all_open_positions=None,
        portfolio_value=0.0,
        universe_path="/nonexistent/universe.json",
    )
    assert result[0]["theme_peers"] == []
    assert result[0]["theme_concentration_pct"] is None


# ---------------------------------------------------------------------------
# Fail-soft: exception in enrichment worker never drops position
# ---------------------------------------------------------------------------

def test_enrichment_exception_does_not_drop_position(tmp_path):
    pos = _position("FAIL")
    universe_path = _universe([], tmp_path)

    with patch("fmp_client.warm_fundamentals_cache"), \
         patch("pm_enrichment._enrich_analyst",         side_effect=Exception("FMP exploded")), \
         patch("pm_enrichment._enrich_price_structure", side_effect=Exception("Alpaca exploded")), \
         patch("pm_enrichment._enrich_fundamentals",    side_effect=Exception("Fundamentals exploded")):
        result = pe.enrich_review_positions([pos], universe_path=universe_path)

    assert len(result) == 1
    assert result[0]["symbol"] == "FAIL"


# ---------------------------------------------------------------------------
# Multiple positions enriched independently
# ---------------------------------------------------------------------------

def test_multiple_positions_enriched(tmp_path):
    positions = [_position("AAPL"), _position("MSFT"), _position("NVDA")]
    universe_path = _universe([], tmp_path)

    with patch("fmp_client.warm_fundamentals_cache"), \
         patch("pm_enrichment._enrich_analyst",         return_value={"analyst_consensus": "BUY", "analyst_pt": 200.0, "analyst_upside_pct": 10.0, "analyst_buy_count": 20, "analyst_sell_count": 3, "analyst_total": 30}), \
         patch("pm_enrichment._enrich_price_structure", return_value={"week52_high": 220.0, "week52_high_distance_pct": -5.0, "stock_above_200d": True, "thesis_intact": True}), \
         patch("pm_enrichment._enrich_fundamentals",    return_value={"pe_ratio": 28.0, "is_profitable": True, "revenue_growth_yoy": 12.0, "revenue_decelerating": False, "fcf_yield": 3.1}):
        result = pe.enrich_review_positions(positions, universe_path=universe_path)

    assert len(result) == 3
    for p in result:
        assert p["analyst_consensus"] == "BUY"
        assert p["thesis_intact"] is True
        assert p["pe_ratio"] == 28.0
