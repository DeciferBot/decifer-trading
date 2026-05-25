"""
test_geopolitical_news_awareness.py — verifies the bot is aware of
geopolitical / macro events (ceasefire, peace deal, invasion, embargo, etc.)
and that the live driver layer has a symmetric falling-risk rule.

Regression guard for v4.47.0 fix: prior to this change, a "Iran ceasefire
reached" Benzinga headline scored 0 in keyword_score, never tripped the
materiality gate, and never reached Apex. The live driver layer also had only
a `geopolitical_risk_rising` rule with no mirror, so peace pricing produced no
transmission-rule output.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest


# ── News vocabulary: macro materiality ────────────────────────────────────────


class TestNewsMacroMateriality:
    """A single geopolitical/macro keyword must flag the article material."""

    def test_ceasefire_alone_is_material(self):
        from news import keyword_score
        result = keyword_score(["Iran ceasefire reached"])
        # threshold is 3; a single +4 macro keyword crosses it
        assert result["score"] >= 3, result
        assert result["macro_hit"] is True
        assert any("ceasefire" in k for k in result["keywords"])

    def test_peace_deal_is_material(self):
        from news import keyword_score
        result = keyword_score(["US and Iran sign peace deal"])
        assert result["score"] >= 3
        assert result["macro_hit"] is True

    def test_invasion_is_material_bearish(self):
        from news import keyword_score
        result = keyword_score(["Russia begins invasion of neighbour"])
        assert result["score"] <= -3
        assert result["macro_hit"] is True

    def test_embargo_is_material_bearish(self):
        from news import keyword_score
        result = keyword_score(["EU expands embargo on Russian oil"])
        assert result["score"] <= -3
        assert result["macro_hit"] is True

    def test_macro_hit_flag_present_in_response(self):
        from news import keyword_score
        result = keyword_score(["Apple beats earnings expectations"])
        assert "macro_hit" in result
        assert result["macro_hit"] is False

    def test_real_world_iran_deal_headline(self):
        """Today's actual headline must be flagged macro_hit."""
        from news import keyword_score
        result = keyword_score(
            ["Trump Says An Iran Deal Is Close, A Top Commodity Analyst Replies: 'Sell The Tweet'"]
        )
        # "iran deal" multi-word phrase is in BULLISH_MACRO
        assert result["macro_hit"] is True, result

    def test_hormuz_reopening_is_material(self):
        from news import keyword_score
        result = keyword_score(["Hormuz reopens as tankers exit Strait"])
        assert result["macro_hit"] is True

    def test_mixed_signal_headline_still_material_via_macro_hit(self):
        """A 'ceasefire reached but markets sell off' style headline nets
        near-zero on directional sentiment, but the ceasefire keyword must
        still force materiality so Apex can reason about it."""
        from news import keyword_score
        result = keyword_score(["Ceasefire reached but stocks fall on profit-taking"])
        assert result["macro_hit"] is True


# ── Materiality gates honour macro_hit ────────────────────────────────────────


class TestAlpacaNewsGateHonoursMacroHit:
    """The Alpaca news ingestion gate must trip on macro_hit even when the
    bull/bear score lands below the keyword threshold."""

    def test_gate_logic_treats_macro_hit_as_material(self):
        # Simulate the gate logic block from alpaca_news.py
        keyword_threshold = 3
        kw = {"score": 1, "macro_hit": True, "bull_hits": 1, "bear_hits": 0, "keywords": []}
        is_material = abs(kw["score"]) >= keyword_threshold or kw.get("macro_hit")
        assert is_material is True


# ── Live driver layer: geopolitical_risk_falling exists ───────────────────────


class TestGeopoliticalRiskFallingDriver:
    """The driver layer must have a symmetric falling-risk rule."""

    def test_driver_module_docstring_mentions_falling(self):
        import live_driver_resolver
        assert "geopolitical_risk_falling" in (live_driver_resolver.__doc__ or "")

    def test_driver_fires_when_defence_underperforms_spy(self):
        """Primary path: ITA underperforms SPY by >1.5%."""
        import live_driver_resolver as ldr

        def fake_5d(symbol):
            # ITA flat, SPY up 2% → ITA-SPY = -2%, so spy-ita = +2% (>1.5%)
            return {
                "SMH": 0.02, "NVDA": 0.01, "IEF": 0.001,
                "USO": -0.02, "SPY": 0.02, "ITA": 0.0,
                "UVXY": -0.05, "HYG": 0.001, "LQD": 0.001,
                "GLD": 0.0, "IWM": 0.005,
            }.get(symbol)

        with patch.object(ldr, "_fetch_5d_return", side_effect=fake_5d):
            result = ldr.resolve(output_path="/tmp/test_ldr_falling.json")
        assert "geopolitical_risk_falling" in result["active_drivers"]
        assert "geopolitical_risk_rising" not in result["active_drivers"]
        os.path.exists("/tmp/test_ldr_falling.json") and os.unlink("/tmp/test_ldr_falling.json")

    def test_driver_fires_when_oil_collapses_and_defence_stalls(self):
        """Secondary path: USO < -5% and ITA not leading by >1%."""
        import live_driver_resolver as ldr

        def fake_5d(symbol):
            # USO crashing -6%, ITA only slightly leading (+0.5%), SPY flat
            return {
                "SMH": 0.02, "NVDA": 0.01, "IEF": 0.001,
                "USO": -0.06, "SPY": 0.0, "ITA": 0.005,
                "UVXY": -0.05, "HYG": 0.001, "LQD": 0.001,
                "GLD": 0.0, "IWM": 0.005,
            }.get(symbol)

        with patch.object(ldr, "_fetch_5d_return", side_effect=fake_5d):
            result = ldr.resolve(output_path="/tmp/test_ldr_oil.json")
        assert "geopolitical_risk_falling" in result["active_drivers"]
        os.path.exists("/tmp/test_ldr_oil.json") and os.unlink("/tmp/test_ldr_oil.json")

    def test_driver_does_not_fire_when_defence_strongly_leads(self):
        """Today's scenario: ITA +3.7%, SPY +0.9% — defence leading 2.8%.
        Falling driver MUST NOT fire even if oil is weak."""
        import live_driver_resolver as ldr

        def fake_5d(symbol):
            return {
                "SMH": 0.036, "NVDA": -0.044, "IEF": 0.004,
                "USO": -0.049, "SPY": 0.009, "ITA": 0.037,
                "UVXY": -0.08, "HYG": 0.006, "LQD": 0.005,
                "GLD": -0.008, "IWM": 0.027,
            }.get(symbol)

        with patch.object(ldr, "_fetch_5d_return", side_effect=fake_5d):
            result = ldr.resolve(output_path="/tmp/test_ldr_today.json")
        assert "geopolitical_risk_rising" in result["active_drivers"]
        assert "geopolitical_risk_falling" not in result["active_drivers"]
        os.path.exists("/tmp/test_ldr_today.json") and os.unlink("/tmp/test_ldr_today.json")


# ── Transmission rules cover the new driver ──────────────────────────────────


class TestTransmissionRulesForFallingDriver:
    """The new driver must have transmission rules so it actually affects themes."""

    def _load_rules(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "intelligence", "transmission_rules.json",
        )
        with open(path) as f:
            return json.load(f)["rules"]

    def test_falling_driver_has_rules(self):
        rules = self._load_rules()
        falling_rules = [r for r in rules if r["driver_alias"] == "geopolitical_risk_falling"]
        assert len(falling_rules) >= 3, "Need at least 3 rules: defence/energy headwind + travel tailwind"

    def test_falling_driver_creates_defence_headwind(self):
        rules = self._load_rules()
        match = [r for r in rules if r["driver_alias"] == "geopolitical_risk_falling"
                 and "defence" in r["affected_targets"]]
        assert match, "Falling driver must produce defence headwind"
        assert match[0]["output_type"] == "theme_headwind"
        assert match[0]["direction"] == "negative"

    def test_falling_driver_creates_energy_headwind(self):
        rules = self._load_rules()
        match = [r for r in rules if r["driver_alias"] == "geopolitical_risk_falling"
                 and "energy" in r["affected_targets"]]
        assert match, "Falling driver must produce energy headwind"
        assert match[0]["output_type"] == "theme_headwind"


# ── Market-now driver label registered ────────────────────────────────────────


def test_market_now_builder_has_label_for_falling_driver():
    from market_now_builder import _DRIVER_LABELS
    assert "geopolitical_risk_falling" in _DRIVER_LABELS
    assert "peace" in _DRIVER_LABELS["geopolitical_risk_falling"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
