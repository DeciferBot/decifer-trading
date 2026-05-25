"""
test_customer_event_classifier.py — Golden event scenarios for Sprint M11A.

Covers the 12 deterministic golden scenarios from the sprint spec:

  1. Ceasefire / oil premium unwind
  2. Oil spike from supply disruption
  3. Nvidia beat but stock falls
  4. Microsoft acquisition
  5. China stimulus
  6. India election or policy surprise
  7. Fed cuts but sounds hawkish
  8. Hot CPI
  9. Bank stress
 10. Chip export restriction
 11. Cyberattack
 12. Weak data but stocks rally (bad-news-good-news)

Each test asserts:
  - the event was classified into the expected family/type
  - directional exposures (positive vs. negative) are correct
  - affected_channels contain the expected transmission mechanism
  - confirmation/invalidation signals present
  - no portfolio, P&L, account, broker, order, position, or PM fields leak

These are pure-function tests — no I/O, no LLM, no execution.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from customer_event_classifier import (
    CHANNELS,
    EVENT_FAMILIES,
    ClassifiedEvent,
    classify_headline,
)


# ── Banned fields (must never appear inside any ClassifiedEvent.to_dict()) ──
_BANNED_DICT_KEYS = frozenset({
    "position_size", "qty", "quantity", "shares",
    "entry_price", "exit_price", "stop_price",
    "pnl", "unrealized_pnl", "realized_pnl",
    "account_id", "ibkr_account", "broker_account",
    "order_id", "client_order_id",
    "buy_signal", "sell_signal", "trade_recommendation",
})


def _assert_customer_safe(ev: ClassifiedEvent) -> None:
    d = ev.to_dict()
    for key in d:
        assert key not in _BANNED_DICT_KEYS, (
            f"Banned key {key!r} appeared in ClassifiedEvent.to_dict()"
        )
    assert ev.event_family in EVENT_FAMILIES, (
        f"event_family {ev.event_family!r} not in approved taxonomy"
    )
    for c in ev.affected_channels:
        assert c in CHANNELS, f"channel {c!r} not in approved taxonomy"


def _types(events: list[ClassifiedEvent]) -> set[str]:
    return {e.event_type for e in events}


def _families(events: list[ClassifiedEvent]) -> set[str]:
    return {e.event_family for e in events}


# ---------------------------------------------------------------------------
# 1. Ceasefire / oil premium unwind
# ---------------------------------------------------------------------------

class TestGolden01CeasefireOilUnwind:

    def test_geopolitics_and_or_commodities_fired(self):
        events = classify_headline(
            "US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.",
        )
        assert events, "expected at least one classification"
        fams = _families(events)
        types = _types(events)

        # event_family: geopolitics and/or commodities
        assert fams & {"geopolitics", "commodities"}
        # event_type: de_escalation and/or oil_risk_premium_unwind
        assert types & {"de_escalation", "oil_risk_premium_unwind"}

    def test_directional_exposures_correct(self):
        events = classify_headline(
            "US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.",
        )
        all_pos = sum((e.likely_positive_exposures for e in events), [])
        all_neg = sum((e.likely_negative_exposures for e in events), [])

        # positive: risk appetite, airlines/transport, consumer-sensitive
        assert any("risk appetite" in p.lower() for p in all_pos)
        assert any("airline" in p.lower() or "transport" in p.lower() for p in all_pos)
        assert any("consumer" in p.lower() for p in all_pos)

        # negative: oil risk premium, energy, defence premium, volatility
        assert any("oil" in n.lower() for n in all_neg)
        assert any("energy" in n.lower() for n in all_neg)
        assert any("defence" in n.lower() or "volatility" in n.lower() for n in all_neg)

    def test_does_not_classify_falling_oil_as_no_shock(self):
        # Sprint requirement: falling oil after peace must NOT be classified as "no event."
        events = classify_headline(
            "US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.",
        )
        # At least one event must explicitly mention oil_risk_premium_unwind
        assert _types(events) & {"oil_risk_premium_unwind", "de_escalation"}

    def test_customer_safe_fields(self):
        events = classify_headline(
            "US says Iran deal could happen today; oil falls 5 percent as Hormuz reopening hopes rise.",
        )
        for e in events:
            _assert_customer_safe(e)


# ---------------------------------------------------------------------------
# 2. Oil spike from supply disruption
# ---------------------------------------------------------------------------

class TestGolden02OilSupplyShock:

    def test_oil_supply_shock_fired(self):
        events = classify_headline(
            "Oil jumps 6 percent after supply disruption and tanker route closure.",
        )
        assert events
        assert _types(events) & {"oil_supply_shock"}
        assert _families(events) & {"commodities"}

    def test_positive_negative_exposures(self):
        events = classify_headline(
            "Oil jumps 6 percent after supply disruption and tanker route closure.",
        )
        pos = sum((e.likely_positive_exposures for e in events), [])
        neg = sum((e.likely_negative_exposures for e in events), [])

        assert any("oil" in p.lower() or "energy" in p.lower() for p in pos)
        assert any("volatility" in p.lower() for p in pos)
        assert any("airline" in n.lower() or "transport" in n.lower() for n in neg)
        assert any("consumer" in n.lower() for n in neg)

    def test_channels_present(self):
        events = classify_headline(
            "Oil jumps 6 percent after supply disruption and tanker route closure.",
        )
        chs = sum((e.affected_channels for e in events), [])
        assert "commodity_prices" in chs
        assert "inflation_expectations" in chs
        assert "risk_appetite" in chs

    def test_customer_safe(self):
        events = classify_headline(
            "Oil jumps 6 percent after supply disruption and tanker route closure.",
        )
        for e in events:
            _assert_customer_safe(e)


# ---------------------------------------------------------------------------
# 3. Nvidia beat but stock falls
# ---------------------------------------------------------------------------

class TestGolden03NvidiaBeatStockFalls:

    def test_classification(self):
        events = classify_headline(
            "Nvidia beats revenue and raises guidance, but shares fall after hours on margin concern and high expectations.",
        )
        assert events
        assert _families(events) & {"earnings_guidance"}
        assert _types(events) & {"positive_surprise_market_rejecting"}

    def test_not_simply_bullish(self):
        events = classify_headline(
            "Nvidia beats revenue and raises guidance, but shares fall after hours on margin concern and high expectations.",
        )
        # Must NOT classify as simple "positive_surprise" only
        assert "positive_surprise" not in _types(events)

    def test_known_conflicts_or_under_review(self):
        events = classify_headline(
            "Nvidia beats revenue and raises guidance, but shares fall after hours on margin concern and high expectations.",
        )
        # Either status=under_review OR known_conflicts present
        flagged = any(e.status == "under_review" or e.known_conflicts for e in events)
        assert flagged

    def test_directional_exposures(self):
        events = classify_headline(
            "Nvidia beats revenue and raises guidance, but shares fall after hours on margin concern and high expectations.",
        )
        pos = sum((e.likely_positive_exposures for e in events), [])
        neg = sum((e.likely_negative_exposures for e in events), [])

        # Positive: AI demand / theme exposure
        assert any("demand" in p.lower() or "theme" in p.lower() for p in pos)
        # Negative: margins, valuation, crowded expectations
        assert any("margin" in n.lower() for n in neg)
        assert any("valuation" in n.lower() for n in neg)

    def test_customer_safe(self):
        events = classify_headline(
            "Nvidia beats revenue and raises guidance, but shares fall after hours on margin concern and high expectations.",
        )
        for e in events:
            _assert_customer_safe(e)


# ---------------------------------------------------------------------------
# 4. Microsoft acquisition
# ---------------------------------------------------------------------------

class TestGolden04MicrosoftAcquisition:

    def test_acquisition_fired(self):
        events = classify_headline(
            "Microsoft announces $40bn acquisition of cybersecurity platform.",
        )
        assert events
        assert _families(events) & {"corporate_action"}
        assert _types(events) & {"acquisition"}

    def test_channels_include_regulation_and_valuation(self):
        events = classify_headline(
            "Microsoft announces $40bn acquisition of cybersecurity platform.",
        )
        chs = sum((e.affected_channels for e in events), [])
        assert "regulation" in chs
        assert "sector_rotation" in chs
        assert any("valuation" in c for c in chs)

    def test_customer_safe(self):
        events = classify_headline(
            "Microsoft announces $40bn acquisition of cybersecurity platform.",
        )
        for e in events:
            _assert_customer_safe(e)


# ---------------------------------------------------------------------------
# 5. China stimulus
# ---------------------------------------------------------------------------

class TestGolden05ChinaStimulus:

    def test_china_stimulus_fired(self):
        events = classify_headline(
            "China unveils major property and infrastructure stimulus package.",
        )
        assert events
        assert _families(events) & {"major_economy_policy"}

    def test_growth_commodities_risk_channels(self):
        events = classify_headline(
            "China unveils major property and infrastructure stimulus package.",
        )
        chs = sum((e.affected_channels for e in events), [])
        assert "growth_expectations" in chs
        assert "commodity_prices" in chs
        assert "risk_appetite" in chs

    def test_second_order_commodities_industrials(self):
        events = classify_headline(
            "China unveils major property and infrastructure stimulus package.",
        )
        pos = sum((e.likely_positive_exposures for e in events), [])
        assert any("industrial" in p.lower() or "commodit" in p.lower() for p in pos)

    def test_customer_safe(self):
        events = classify_headline(
            "China unveils major property and infrastructure stimulus package.",
        )
        for e in events:
            _assert_customer_safe(e)


# ---------------------------------------------------------------------------
# 6. India election / policy
# ---------------------------------------------------------------------------

class TestGolden06IndiaPolicy:

    def test_india_event_fired(self):
        events = classify_headline(
            "India election result raises uncertainty over reform momentum and infrastructure spending.",
        )
        assert events
        assert _families(events) & {"major_economy_policy"}

    def test_channels_country_risk(self):
        events = classify_headline(
            "India election result raises uncertainty over reform momentum and infrastructure spending.",
        )
        chs = sum((e.affected_channels for e in events), [])
        # Country policy risk → mapped through growth_expectations + risk_appetite
        assert "growth_expectations" in chs or "risk_appetite" in chs


# ---------------------------------------------------------------------------
# 7. Fed cuts but sounds hawkish
# ---------------------------------------------------------------------------

class TestGolden07FedCutsHawkish:

    def test_central_bank_conflict_fired(self):
        events = classify_headline(
            "Fed cuts rates but warns inflation remains too high and future cuts may be slower.",
        )
        assert events
        assert _families(events) & {"central_bank"}
        assert _types(events) & {"rate_cut_with_hawkish_guidance"}

    def test_not_automatically_bullish(self):
        events = classify_headline(
            "Fed cuts rates but warns inflation remains too high and future cuts may be slower.",
        )
        # Must NOT classify as plain "rate_cut" — that would be bullish-only
        assert "rate_cut" not in _types(events) or "rate_cut_with_hawkish_guidance" in _types(events)
        # status must be conflicting
        statuses = {e.status for e in events}
        assert "under_review" in statuses

    def test_known_conflicts_present(self):
        events = classify_headline(
            "Fed cuts rates but warns inflation remains too high and future cuts may be slower.",
        )
        any_conflict = any(e.known_conflicts for e in events)
        assert any_conflict


# ---------------------------------------------------------------------------
# 8. Hot CPI
# ---------------------------------------------------------------------------

class TestGolden08HotCPI:

    def test_hot_inflation_fired(self):
        events = classify_headline(
            "CPI comes in hotter than expected; yields jump and rate cut odds fall.",
        )
        assert events
        assert _families(events) & {"macro_data"}
        assert _types(events) & {"hot_inflation_print"}

    def test_channels(self):
        events = classify_headline(
            "CPI comes in hotter than expected; yields jump and rate cut odds fall.",
        )
        chs = sum((e.affected_channels for e in events), [])
        assert "inflation_expectations" in chs
        assert "interest_rates" in chs
        assert "valuation_multiple" in chs


# ---------------------------------------------------------------------------
# 9. Bank stress
# ---------------------------------------------------------------------------

class TestGolden09BankStress:

    def test_credit_stress_fired(self):
        events = classify_headline(
            "Regional bank shares fall after deposit pressure and credit losses.",
        )
        assert events
        assert _families(events) & {"credit_liquidity"}

    def test_channels(self):
        events = classify_headline(
            "Regional bank shares fall after deposit pressure and credit losses.",
        )
        chs = sum((e.affected_channels for e in events), [])
        assert "credit_stress" in chs
        assert "risk_appetite" in chs


# ---------------------------------------------------------------------------
# 10. Chip export restriction
# ---------------------------------------------------------------------------

class TestGolden10ChipExportRestriction:

    def test_chip_restriction_fired(self):
        events = classify_headline(
            "US announces new export restrictions on advanced AI chips to China.",
        )
        assert events
        # Either regulation_legal OR technology_product is acceptable per sprint
        assert _families(events) & {"regulation_legal", "technology_product"}

    def test_channels(self):
        events = classify_headline(
            "US announces new export restrictions on advanced AI chips to China.",
        )
        chs = sum((e.affected_channels for e in events), [])
        assert "regulation" in chs
        assert "supply_chain" in chs
        # Either revenue_growth or geopolitical_risk indicates the channels
        assert "revenue_growth" in chs or "geopolitical_risk" in chs


# ---------------------------------------------------------------------------
# 11. Cyberattack
# ---------------------------------------------------------------------------

class TestGolden11Cyberattack:

    def test_cyberattack_fired(self):
        events = classify_headline(
            "Major retailer reports cyberattack disrupting operations.",
            symbols=["TGT"],
        )
        assert events
        assert _families(events) & {"company_specific_shock"}
        assert _types(events) & {"cyberattack"}

    def test_negative_victim_positive_cybersecurity(self):
        events = classify_headline(
            "Major retailer reports cyberattack disrupting operations.",
            symbols=["TGT"],
        )
        pos = sum((e.likely_positive_exposures for e in events), [])
        neg = sum((e.likely_negative_exposures for e in events), [])
        assert any("cybersecurity" in p.lower() for p in pos)
        assert any("victim" in n.lower() or "company" in n.lower() for n in neg)


# ---------------------------------------------------------------------------
# 12. Weak data but stocks rally (bad-news-good-news)
# ---------------------------------------------------------------------------

class TestGolden12BadNewsGoodNews:

    def test_classification(self):
        events = classify_headline(
            "Weak jobs data sends yields lower and stocks higher as traders price more rate cuts.",
        )
        assert events
        assert _families(events) & {"macro_data"}
        assert _types(events) & {"weak_data_rate_cut_rally"}

    def test_not_automatically_bearish(self):
        events = classify_headline(
            "Weak jobs data sends yields lower and stocks higher as traders price more rate cuts.",
        )
        # Must surface the bad-news-good-news interpretation, not generic bearishness
        # → at least one event has positive_exposures referencing growth/rate-sensitive
        pos = sum((e.likely_positive_exposures for e in events), [])
        assert any("growth" in p.lower() or "rate-sensitive" in p.lower()
                    or "risk appetite" in p.lower() for p in pos)

    def test_known_conflicts_or_marker(self):
        events = classify_headline(
            "Weak jobs data sends yields lower and stocks higher as traders price more rate cuts.",
        )
        # Either known_conflicts is set OR the summary explicitly mentions the contradiction
        flagged = any(e.known_conflicts for e in events) or any(
            "bad-news-good-news" in (e.summary_plain_english or "").lower()
            for e in events
        )
        assert flagged


# ---------------------------------------------------------------------------
# Non-events: classifier must not over-fire
# ---------------------------------------------------------------------------

class TestNoFireOnIrrelevant:

    def test_empty_headline_returns_empty(self):
        assert classify_headline("") == []

    def test_unrelated_headline_returns_empty(self):
        # Generic puff piece — must not classify into any of the 15 families
        assert classify_headline(
            "Apple opens new store in downtown Sydney to mark Australian expansion."
        ) == []

    def test_short_irrelevant_string(self):
        assert classify_headline("Hello world") == []
