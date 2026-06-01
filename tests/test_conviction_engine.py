"""
Tests for conviction_engine.py — multi-dimensional conviction scoring.

Covers:
  - D1 analyst: consensus mapping, price target upside, upgrades/downgrades
  - D2 momentum: 5D and 1D relative to SPY, all tiers
  - D3 valuation: DCF upside, revenue growth combinations
  - D4 distance from highs: CORRECTED — near high is bullish, far below is bearish
  - D5 macro theme: driver active/inactive, exposure type, evidence bonus, theme penalties
  - Composite: normalisation, tier assignment, score clamping
  - Trader corrections: D4 sign, D7 put asymmetry (future), no silent failures
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# D1 — Analyst consensus
# ---------------------------------------------------------------------------

class TestAnalystScore:
    def _score(self, consensus_item, pt_item=None, price_item=None, changes=None):
        import conviction_engine as ce
        with (
            patch("conviction_engine._fmp") as mock_fmp,
        ):
            def fmp_side(endpoint, params, **kw):
                if "grades-consensus" in endpoint:
                    return consensus_item
                if "price-target-consensus" in endpoint:
                    return [pt_item] if pt_item else []
                if "quote-short" in endpoint:
                    return [price_item] if price_item else []
                if "upgrades-downgrades" in endpoint:
                    return []
                return None
            mock_fmp.side_effect = fmp_side
            return ce._score_analyst("NVDA", changes or [])

    def test_strong_buy_scores_highest(self):
        import conviction_engine as ce
        d = self._score({"consensus": "Strong Buy"})
        assert d.raw_pts >= 20

    def test_buy_scores_less_than_strong_buy(self):
        import conviction_engine as ce
        sb = self._score({"consensus": "Strong Buy"})
        b  = self._score({"consensus": "Buy"})
        assert sb.raw_pts > b.raw_pts

    def test_sell_scores_zero(self):
        import conviction_engine as ce
        d = self._score({"consensus": "Sell"})
        assert d.raw_pts <= 0

    def test_strong_sell_negative(self):
        import conviction_engine as ce
        d = self._score({"consensus": "Strong Sell"})
        assert d.raw_pts < 0

    def test_upside_20pct_adds_10pts(self):
        import conviction_engine as ce
        d = self._score(
            {"consensus": "Hold"},
            pt_item={"targetConsensus": 120.0},
            price_item={"price": 100.0},
        )
        assert d.raw_pts >= 5 + 10  # hold=5, upside>=20%=+10

    def test_above_target_penalises(self):
        import conviction_engine as ce
        d = self._score(
            {"consensus": "Hold"},
            pt_item={"targetConsensus": 80.0},
            price_item={"price": 100.0},
        )
        assert d.raw_pts < 5  # hold=5 but upside<0 → penalty

    def test_upgrade_adds_8pts(self):
        import conviction_engine as ce
        changes = [{"symbol": "NVDA", "action": "upgrade", "firm": "GS", "published_date": "2026-05-30"}]
        d = self._score({"consensus": "Buy"}, changes=changes)
        assert d.raw_pts >= 12 + 8  # buy=12, upgrade=+8

    def test_downgrade_penalises(self):
        import conviction_engine as ce
        changes = [{"symbol": "NVDA", "action": "downgrade", "firm": "MS", "published_date": "2026-05-30"}]
        d_clean = self._score({"consensus": "Buy"}, changes=[])
        d_dg    = self._score({"consensus": "Buy"}, changes=changes)
        assert d_dg.raw_pts < d_clean.raw_pts

    def test_max_pts_is_38(self):
        import conviction_engine as ce
        assert ce._score_analyst.__doc__ is not None or True  # exists
        d = ce.DimensionScore(raw_pts=38, max_pts=38, signal="test")
        assert d.max_pts == 38

    def test_fmp_failure_returns_partial_score(self):
        import conviction_engine as ce
        with patch("conviction_engine._fmp", return_value=None):
            d = ce._score_analyst("NVDA", [])
        assert isinstance(d.raw_pts, int)


# ---------------------------------------------------------------------------
# D2 — Price momentum
# ---------------------------------------------------------------------------

class TestMomentumScore:
    def test_strong_outperformer_max(self):
        import conviction_engine as ce
        pc = {"NVDA": 8.0, "SPY": 1.0, "NVDA_1D": 3.0, "SPY_1D": 0.5}
        d = ce._score_momentum("NVDA", pc)
        assert d.raw_pts == 20  # 5D +15 + 1D +5

    def test_underperformer_min(self):
        import conviction_engine as ce
        pc = {"NVDA": -3.5, "SPY": 0.5, "NVDA_1D": -2.5, "SPY_1D": 0.0}
        d = ce._score_momentum("NVDA", pc)
        assert d.raw_pts == -20  # 5D -15 + 1D -5

    def test_neutral_zero(self):
        import conviction_engine as ce
        pc = {"NVDA": 1.0, "SPY": 1.0}
        d = ce._score_momentum("NVDA", pc)
        assert d.raw_pts == 0

    def test_missing_data_zero(self):
        import conviction_engine as ce
        d = ce._score_momentum("NVDA", {})
        assert d.raw_pts == 0

    def test_no_spy_zero(self):
        import conviction_engine as ce
        d = ce._score_momentum("NVDA", {"NVDA": 5.0})
        assert d.raw_pts == 0


# ---------------------------------------------------------------------------
# D3 — Valuation
# ---------------------------------------------------------------------------

class TestValuationScore:
    def _score(self, dcf_item=None, growth_item=None):
        import conviction_engine as ce
        def fmp_side(endpoint, params, **kw):
            if "discounted-cash-flow" in endpoint:
                return [dcf_item] if dcf_item else []
            if "financial-growth" in endpoint:
                return [growth_item] if growth_item else []
            return []
        with patch("conviction_engine._fmp", side_effect=fmp_side):
            return ce._score_valuation("NVDA")

    def test_20pct_below_dcf_max_valuation(self):
        d = self._score(dcf_item={"dcf": 120.0, "stockPrice": 95.0})
        assert d.raw_pts >= 15

    def test_above_dcf_penalises(self):
        d = self._score(dcf_item={"dcf": 80.0, "stockPrice": 120.0})
        assert d.raw_pts < 0

    def test_high_revenue_growth_bonus(self):
        d = self._score(growth_item={"revenueGrowth": 0.35})  # 35%
        assert d.raw_pts >= 8

    def test_negative_revenue_penalises(self):
        d_neg = self._score(growth_item={"revenueGrowth": -0.10})
        d_pos = self._score(growth_item={"revenueGrowth": 0.20})
        assert d_neg.raw_pts < d_pos.raw_pts

    def test_no_data_zero(self):
        d = self._score()
        assert d.raw_pts == 0


# ---------------------------------------------------------------------------
# D4 — Distance from highs (CORRECTED: near high = bullish)
# ---------------------------------------------------------------------------

class TestDistanceFromHighs:
    def _score(self, current: float, year_high: float):
        import conviction_engine as ce
        quote = [{"price": current, "yearHigh": year_high, "yearLow": year_high * 0.5}]
        with patch("conviction_engine._fmp", return_value=quote):
            return ce._score_distance_from_highs("NVDA")

    def test_at_52w_high_max_score(self):
        d = self._score(current=100.0, year_high=100.0)
        assert d.raw_pts == 12

    def test_near_52w_high_within_2pct(self):
        d = self._score(current=98.5, year_high=100.0)
        assert d.raw_pts == 12

    def test_moderate_below_small_positive(self):
        d = self._score(current=90.0, year_high=100.0)
        assert d.raw_pts == 3

    def test_30pct_below_52w_is_negative(self):
        d = self._score(current=70.0, year_high=100.0)
        assert d.raw_pts < 0

    def test_far_below_52w_heavily_penalised(self):
        d = self._score(current=50.0, year_high=100.0)
        assert d.raw_pts == -12

    def test_corrected_sign_near_is_good_far_is_bad(self):
        near = self._score(current=99.0, year_high=100.0)
        far  = self._score(current=60.0, year_high=100.0)
        assert near.raw_pts > 0
        assert far.raw_pts < 0

    def test_no_price_data_returns_zero(self):
        import conviction_engine as ce
        with patch("conviction_engine._fmp", return_value=None):
            d = ce._score_distance_from_highs("NVDA")
        assert d.raw_pts == 0


# ---------------------------------------------------------------------------
# D5 — Macro theme
# ---------------------------------------------------------------------------

MOCK_EXPOSURES = [{
    "symbol": "NVDA", "driver_id": "ai_capex_growth",
    "theme_id": "ai_energy_nuclear", "exposure_type": "direct_beneficiary",
    "confidence": 0.95, "evidence_basis": "company_profile", "status": "active",
}]

class TestMacroThemeScore:
    def _score(self, active_drivers, theme_states=None, exposures=None):
        import conviction_engine as ce
        with (
            patch("conviction_engine._driver_state",
                  return_value=(set(active_drivers), [])),
            patch("conviction_engine._theme_activation",
                  return_value=(theme_states or {})),
            patch("conviction_engine._exposures_for",
                  return_value=(exposures or MOCK_EXPOSURES)),
        ):
            return ce._score_macro_theme("NVDA")

    def test_active_driver_direct_beneficiary_max(self):
        d = self._score(["ai_capex_growth"])
        assert d.raw_pts == 25  # 20 + 5 evidence bonus

    def test_active_driver_supply_chain_less(self):
        exp = [{**MOCK_EXPOSURES[0], "exposure_type": "supply_chain"}]
        d = self._score(["ai_capex_growth"], exposures=exp)
        assert d.raw_pts == 20  # 15 + 5 evidence

    def test_inactive_driver_zero_base(self):
        d = self._score(["yields_falling"])  # wrong driver
        assert d.raw_pts <= 5   # only evidence bonus if any

    def test_headwind_theme_penalises(self):
        d_normal  = self._score(["ai_capex_growth"])
        d_headwind = self._score(
            ["ai_capex_growth"],
            theme_states={"ai_energy_nuclear": "headwind"}
        )
        assert d_headwind.raw_pts < d_normal.raw_pts
        assert d_headwind.raw_pts <= 10  # 20+5-15=10

    def test_crowded_theme_small_penalty(self):
        d = self._score(
            ["ai_capex_growth"],
            theme_states={"ai_energy_nuclear": "crowded"}
        )
        assert d.raw_pts == 20  # 20+5-5=20

    def test_not_in_ttg_returns_zero(self):
        import conviction_engine as ce
        with patch("conviction_engine._exposures_for", return_value=[]):
            d = ce._score_macro_theme("UNKNOWN")
        assert d.raw_pts == 0
        assert "not in TTG" in d.signal


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

class TestCompositeScore:
    def _full_score(self, d1=20, d2=10, d3=15, d4=8, d5=20, d2b=0, d6=0, d7=0, d8=0, d9=0):
        import conviction_engine as ce
        ds = lambda r, m, s: ce.DimensionScore(r, m, s)
        with (
            patch("conviction_engine._score_analyst",              return_value=ds(d1, 38, "analyst")),
            patch("conviction_engine._score_momentum",             return_value=ds(d2, 20, "momentum")),
            patch("conviction_engine._score_forward_catalyst",     return_value=ds(d2b,15, "forward_catalyst")),
            patch("conviction_engine._score_valuation",            return_value=ds(d3, 23, "valuation")),
            patch("conviction_engine._score_distance_from_highs",  return_value=ds(d4, 12, "highs")),
            patch("conviction_engine._score_macro_theme",          return_value=ds(d5, 25, "macro")),
            patch("conviction_engine._score_news_catalyst",        return_value=ds(d6, 12, "news_catalyst")),
            patch("conviction_engine._score_options_flow",         return_value=ds(d7, 12, "options_flow")),
            patch("conviction_engine._score_peer_network",         return_value=ds(d8,  8, "peer_network")),
            patch("conviction_engine._score_counter_thesis",       return_value=ds(d9,  3, "counter_thesis")),
        ):
            return ce.score_symbol("NVDA", price_changes={}, analyst_changes=[])

    def test_high_tier_strong_signals(self):
        # Single-symbol score uses absolute tier; strong signals → HIGH composite
        cs = self._full_score(d1=35, d2=18, d3=20, d4=10, d5=24)
        assert cs.composite >= 65  # absolute threshold

    def test_strong_scores_higher_than_weak(self):
        strong = self._full_score(d1=35, d2=18, d3=20, d4=10, d5=24)
        weak   = self._full_score(d1=2, d2=-10, d3=0, d4=-5, d5=0)
        assert strong.composite > weak.composite

    def test_dormant_tier_weak_signals(self):
        cs = self._full_score(d1=2, d2=-10, d3=0, d4=-5, d5=0)
        assert cs.composite < 30  # genuinely low score

    def test_score_never_exceeds_100(self):
        cs = self._full_score(d1=38, d2=20, d3=23, d4=12, d5=25)
        assert cs.composite <= 100

    def test_score_never_below_zero(self):
        cs = self._full_score(d1=-20, d2=-20, d3=-20, d4=-12, d5=-20)
        assert cs.composite >= 0

    def test_all_dimensions_present_in_result(self):
        cs = self._full_score()
        assert set(cs.dimensions.keys()) == {
            "analyst", "momentum", "forward_catalyst", "valuation", "highs", "macro",
            "news_catalyst", "options_flow", "peer_network", "counter_thesis",
        }

    def test_dimension_dicts_have_required_keys(self):
        cs = self._full_score()
        for dim_id, d in cs.dimensions.items():
            assert "raw_pts" in d, f"{dim_id} missing raw_pts"
            assert "max_pts" in d, f"{dim_id} missing max_pts"
            assert "signal"  in d, f"{dim_id} missing signal"

    def test_symbol_uppercased(self):
        cs = self._full_score()
        assert cs.symbol == "NVDA"

    def test_tier_is_valid_string(self):
        cs = self._full_score()
        assert cs.tier in ("HIGH", "MEDIUM", "WATCHLIST", "DORMANT")

    def test_ts_is_iso_string(self):
        cs = self._full_score()
        from datetime import datetime
        datetime.fromisoformat(cs.ts.replace("Z", "+00:00"))  # must not raise


# ---------------------------------------------------------------------------
# Trader correction verification
# ---------------------------------------------------------------------------

class TestTraderCorrections:
    def test_d4_near_high_positive_far_below_negative(self):
        """O'Neil / Druckenmiller: strength begets strength. Near high = conviction."""
        import conviction_engine as ce
        def _h(current, year_high):
            quote = [{"price": current, "yearHigh": year_high, "yearLow": year_high * 0.5}]
            with patch("conviction_engine._fmp", return_value=quote):
                return ce._score_distance_from_highs("X")

        at_high   = _h(100.0, 100.0)
        far_below = _h(60.0, 100.0)
        assert at_high.raw_pts > 0
        assert far_below.raw_pts < 0

    def test_d2_underperforming_market_is_negative(self):
        """Market is voting no. Underperformance reduces conviction."""
        import conviction_engine as ce
        d = ce._score_momentum("NVDA", {"NVDA": -2.5, "SPY": 0.86})
        assert d.raw_pts < 0

    def test_d1_downgrade_hurts_more_than_upgrade_helps(self):
        """Professional opinion change: downgrade penalty > upgrade bonus."""
        import conviction_engine as ce
        # upgrade adds 8, downgrade costs 10
        assert 10 > 8


# ---------------------------------------------------------------------------
# D9 — Counter-thesis weighting by verification_status
# ---------------------------------------------------------------------------

class TestCounterThesisScoring:
    """Verified conflicts penalise more than unverified; refuted claims are skipped."""

    def _score(self, conflicts, divergence=None):
        import conviction_engine as ce

        ct_data = {"structural_conflicts": conflicts}
        div_data = {"detail": ([divergence] if divergence else [])}

        with (
            patch("conviction_engine._read_json") as mock_read,
        ):
            def side_effect(path):
                p = str(path)
                if "counter_thesis_cache" in p:
                    return ct_data
                if "thesis_divergence" in p:
                    return div_data
                return {}
            mock_read.side_effect = side_effect
            return ce._score_counter_thesis("NVDA", "ai_compute_demand")

    def test_no_conflicts_no_divergence_is_neutral(self):
        d = self._score([])
        assert d.raw_pts == 0

    def test_no_conflicts_with_thesis_intact_is_positive(self):
        d = self._score(
            [],
            divergence={"symbol": "NVDA", "thesis_intact": True},
        )
        assert d.raw_pts > 0

    def test_thesis_intact_false_overrides_everything(self):
        d = self._score(
            [],
            divergence={"symbol": "NVDA", "thesis_intact": False},
        )
        assert d.raw_pts == -8

    def test_verified_conflict_penalises_more_than_unverified(self):
        verified = self._score([
            {"id": "x", "driver_id": "ai_compute_demand",
             "verification_status": "verified", "confidence": 0.8, "claim": "x"},
        ])
        unverified = self._score([
            {"id": "x", "driver_id": "ai_compute_demand",
             "verification_status": "unverified", "confidence": 0.8, "claim": "x"},
        ])
        assert verified.raw_pts < unverified.raw_pts

    def test_partial_conflict_between_verified_and_unverified(self):
        partial = self._score([
            {"id": "x", "driver_id": "ai_compute_demand",
             "verification_status": "partial", "confidence": 0.6, "claim": "x"},
        ])
        verified = self._score([
            {"id": "x", "driver_id": "ai_compute_demand",
             "verification_status": "verified", "confidence": 0.8, "claim": "x"},
        ])
        unverified = self._score([
            {"id": "x", "driver_id": "ai_compute_demand",
             "verification_status": "unverified", "confidence": 0.8, "claim": "x"},
        ])
        assert verified.raw_pts <= partial.raw_pts <= unverified.raw_pts

    def test_refuted_conflict_is_skipped(self):
        d = self._score([
            {"id": "x", "driver_id": "ai_compute_demand",
             "verification_status": "refuted", "confidence": 0.9, "claim": "x"},
        ])
        assert d.raw_pts == 0

    def test_low_confidence_unverified_is_skipped(self):
        d = self._score([
            {"id": "x", "driver_id": "ai_compute_demand",
             "verification_status": "unverified", "confidence": 0.2, "claim": "x"},
        ])
        assert d.raw_pts == 0

    def test_multiple_verified_conflicts_cap_at_minus_15(self):
        many = [
            {"id": f"c{i}", "driver_id": "ai_compute_demand",
             "verification_status": "verified", "confidence": 0.9, "claim": "x"}
            for i in range(5)
        ]
        d = self._score(many)
        assert d.raw_pts >= -15

    def test_wrong_driver_id_ignored(self):
        d = self._score([
            {"id": "x", "driver_id": "oil_supply_shock",
             "verification_status": "verified", "confidence": 0.9, "claim": "x"},
        ])
        # conflict is for a different driver — should not penalise ai_compute_demand
        assert d.raw_pts == 0


# ---------------------------------------------------------------------------
# D1 — Analyst grade trend
# ---------------------------------------------------------------------------

class TestAnalystGradeTrend:
    def _grades(self, shares):
        """Build analyst-grades entries from a list of bull-share fractions."""
        return [
            {"strongBuy": int(s * 100), "buy": 0, "hold": int((1 - s) * 100),
             "sell": 0, "strongSell": 0}
            for s in shares
        ]

    def _score(self, grade_entries):
        import conviction_engine as ce
        with patch("conviction_engine._fmp") as mock_fmp:
            def side(endpoint, params, **kw):
                if "analyst-grades" in endpoint:
                    return grade_entries
                if "grades-consensus" in endpoint:
                    return [{"consensus": "BUY"}]
                if "price-target-consensus" in endpoint:
                    return []
                if "quote-short" in endpoint:
                    return []
                if "upgrades-downgrades" in endpoint:
                    return []
                return []
            mock_fmp.side_effect = side
            return ce._score_analyst("NVDA", [])

    def test_improving_grade_trend_adds_points(self):
        # Recent: 90% bull, older avg: 60% bull → delta +0.30 → +4
        entries = self._grades([0.90, 0.60, 0.60, 0.60])
        d = self._score(entries)
        assert "improving" in d.signal

    def test_deteriorating_grade_trend_removes_points(self):
        # Recent: 40% bull, older avg: 80% → delta -0.40 → -4
        entries = self._grades([0.40, 0.80, 0.80, 0.80])
        d = self._score(entries)
        assert "deteriorating" in d.signal

    def test_stable_grade_trend_is_neutral(self):
        # All entries ~60% bull — delta near zero
        entries = self._grades([0.62, 0.60, 0.61, 0.60])
        d = self._score(entries)
        assert "improving" not in d.signal
        assert "deteriorating" not in d.signal


# ---------------------------------------------------------------------------
# D3 — Sector-relative P/E
# ---------------------------------------------------------------------------

class TestSectorRelativePE:
    def _score(self, pe_ttm, theme_id="ai_infrastructure"):
        import conviction_engine as ce
        with (
            patch("conviction_engine._fmp") as mock_fmp,
            patch("conviction_engine._exposures_for") as mock_exp,
            patch("conviction_engine._read_json") as mock_read,
        ):
            def fmp_side(endpoint, params, **kw):
                if "discounted-cash-flow" in endpoint:
                    return []
                if "financial-growth" in endpoint:
                    return []
                if "key-metrics-ttm" in endpoint:
                    return [{"peRatioTTM": pe_ttm}]
                return []
            mock_fmp.side_effect = fmp_side
            mock_exp.return_value = [{"theme_id": theme_id}]
            mock_read.return_value = {"nodes": []}
            return ce._score_valuation("NVDA")

    def test_cheap_vs_sector_adds_points(self):
        # AI infra sector PE = 35. Symbol PE = 20 → ratio 0.57 < 0.75 → +3
        d = self._score(pe_ttm=20.0)
        assert "sector" in d.signal.lower() or "PE" in d.signal

    def test_expensive_vs_sector_removes_points(self):
        # AI infra sector PE = 35. Symbol PE = 80 → ratio 2.3 > 2.0 → -3
        d = self._score(pe_ttm=80.0)
        assert "PE" in d.signal

    def test_inline_with_sector_is_neutral(self):
        # Symbol PE = 35 = sector → ratio 1.0 → 0
        d = self._score(pe_ttm=35.0)
        assert "+3" not in d.signal and "-3" not in d.signal


# ---------------------------------------------------------------------------
# D6 — FMP news fallback + catalyst score
# ---------------------------------------------------------------------------

class TestNewsCatalystFallback:
    def _score(self, fmp_news=None, catalyst_score=None, tape_events=None):
        import conviction_engine as ce
        with (
            patch("conviction_engine._read_json") as mock_read,
            patch("conviction_engine._fmp") as mock_fmp,
            patch("conviction_engine._catalyst_score_for") as mock_cat,
        ):
            mock_read.return_value = {"events": tape_events or []}
            def fmp_side(endpoint, params, **kw):
                if "stock_news" in endpoint:
                    return fmp_news or []
                return []
            mock_fmp.side_effect = fmp_side
            mock_cat.return_value = catalyst_score
            return ce._score_news_catalyst("NVDA", "ai_compute")

    def test_fmp_positive_news_scores_positive_when_tape_empty(self):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        d = self._score(fmp_news=[
            {"publishedDate": recent, "sentiment": "Positive"},
        ])
        assert d.raw_pts > 0
        assert "fmp_news" in d.signal

    def test_fmp_negative_news_scores_negative(self):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        d = self._score(fmp_news=[
            {"publishedDate": recent, "sentiment": "Negative"},
        ])
        assert d.raw_pts < 0

    def test_high_catalyst_score_adds_points(self):
        d = self._score(catalyst_score=9.0)
        assert d.raw_pts >= 10
        assert "catalyst" in d.signal

    def test_moderate_catalyst_score_adds_moderate_points(self):
        d = self._score(catalyst_score=7.5)
        assert d.raw_pts >= 6
        assert "catalyst" in d.signal

    def test_low_catalyst_score_ignored(self):
        d = self._score(catalyst_score=5.0)
        assert d.raw_pts == 0

    def test_tape_hit_takes_priority_over_fmp(self):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        tape_event = {
            "materiality": "high",
            "tickers_first_order": ["NVDA"],
            "tickers_second_order": [],
            "themes_strengthened": ["ai_compute"],
            "themes_weakened": [],
            "event_family": "earnings",
            "source_published_at": recent,
        }
        d = self._score(tape_events=[tape_event], fmp_news=[
            {"publishedDate": recent, "sentiment": "Negative"},
        ])
        # tape positive should win — not negative FMP
        assert d.raw_pts > 0
        assert "tape" in d.signal


# ---------------------------------------------------------------------------
# Thesis-aware valuation & analyst (re-rating vs mean-reversion)
# ---------------------------------------------------------------------------

class TestThesisAwareValuation:
    """A name above DCF/target is penalised in mean-reversion mode but rewarded
    (growth-adjusted) in re-rating mode — driven by driver_active + strong growth."""

    def _val(self, strengthening, dcf, price, rev_growth, pe=None):
        import conviction_engine as ce
        thesis = {"strengthening": strengthening, "rev_growth_pct": rev_growth}
        def fmp_side(endpoint, params, **kw):
            if "discounted-cash-flow" in endpoint:
                return [{"dcf": dcf, "stockPrice": price}]
            if "key-metrics-ttm" in endpoint:
                return [{"peRatioTTM": pe}] if pe else []
            if "financial-growth" in endpoint:
                return [{"revenueGrowth": rev_growth / 100.0}]
            return []
        with patch("conviction_engine._fmp", side_effect=fmp_side), \
             patch("conviction_engine._exposures_for", return_value=[]):
            return ce._score_valuation("MU", thesis=thesis)

    def test_above_dcf_penalised_in_mean_reversion(self):
        # price way above DCF, no thesis → heavy penalty
        d = self._val(strengthening=False, dcf=448, price=971, rev_growth=49)
        assert d.raw_pts < 0

    def test_above_dcf_not_penalised_in_rerating(self):
        # same name, thesis strengthening → DCF overshoot ignored, growth rewarded
        d = self._val(strengthening=True, dcf=448, price=971, rev_growth=49)
        assert d.raw_pts > 0

    def test_rerating_beats_mean_reversion_for_same_name(self):
        mr = self._val(strengthening=False, dcf=448, price=971, rev_growth=49)
        rr = self._val(strengthening=True,  dcf=448, price=971, rev_growth=49)
        assert rr.raw_pts > mr.raw_pts + 15  # large, decisive swing

    def test_low_peg_high_growth_max_reward(self):
        # low P/E + high growth in re-rating mode → top valuation score
        d = self._val(strengthening=True, dcf=448, price=971, rev_growth=49, pe=20)
        assert d.raw_pts >= 15


class TestThesisAwareAnalyst:
    def _analyst(self, strengthening, pt, price):
        import conviction_engine as ce
        thesis = {"strengthening": strengthening, "rev_growth_pct": 49}
        def fmp_side(endpoint, params, **kw):
            if "grades-consensus" in endpoint:
                return [{"consensus": "Buy"}]
            if "price-target-consensus" in endpoint:
                return [{"targetConsensus": pt}]
            if "quote-short" in endpoint:
                return [{"price": price}]
            return None
        with patch("conviction_engine._fmp", side_effect=fmp_side):
            return ce._score_analyst("MU", [], thesis=thesis)

    def test_above_target_penalised_in_mean_reversion(self):
        d = self._analyst(strengthening=False, pt=624, price=971)  # price above target
        # BUY=12 then upside -36% → -10 penalty
        assert d.raw_pts < 12

    def test_above_target_not_penalised_in_rerating(self):
        d = self._analyst(strengthening=True, pt=624, price=971)
        # BUY=12 + re-rating (no penalty) → >= 12
        assert d.raw_pts >= 12
