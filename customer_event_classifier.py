# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  customer_event_classifier.py              ║
# ║   Customer-only deterministic event classifier              ║
# ║   Sprint M11A — Customer Event Tape                          ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
customer_event_classifier.py — Deterministic classification of news headlines
into customer-safe market events.

This module is customer-intelligence only.

Boundaries (enforced by scripts/verify_customer_event_tape_safety.py):
  - No imports from execution, order, broker, PM, or universe modules.
  - No LLM dependency. Pure deterministic functions.
  - No I/O. No network calls. No file writes.

Inputs:  headline string (+ optional snippet, symbols, source metadata)
Outputs: list of ClassifiedEvent dataclasses, possibly empty.

Each ClassifiedEvent describes the market read of a single event:
  - event_family / event_type / status
  - directional exposures (positive vs. negative)
  - affected channels (transmission mechanism)
  - sectors / themes / tickers
  - confirmation + invalidation signals
  - materiality + source confidence

Multiple events may be returned for a single headline (e.g., "Iran deal
sends oil lower" produces both a geopolitics/de_escalation event AND a
commodities/oil_risk_premium_unwind event).

The reconciliation between event evidence and price drivers happens in
market_now_reconciler.py, not here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

EVENT_FAMILIES: frozenset[str] = frozenset({
    "geopolitics",
    "commodities",
    "earnings_guidance",
    "corporate_action",
    "central_bank",
    "macro_data",
    "major_economy_policy",
    "regulation_legal",
    "credit_liquidity",
    "technology_product",
    "company_specific_shock",
    "market_structure",
})

EVENT_STATUSES: frozenset[str] = frozenset({
    "rumour", "reported", "confirmed", "denied",
    "implemented", "reversed", "under_review",
})

CHANNELS: frozenset[str] = frozenset({
    "growth_expectations", "inflation_expectations", "interest_rates",
    "liquidity", "currency", "commodity_prices", "margins", "revenue_growth",
    "earnings_guidance", "valuation_multiple", "risk_appetite", "volatility",
    "supply_chain", "regulation", "capital_expenditure", "consumer_demand",
    "credit_stress", "geopolitical_risk", "sector_rotation",
    "theme_acceleration", "theme_reversal",
})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ClassifiedEvent:
    """A customer-safe event classification.

    No raw prices, no broker state, no execution fields. The exposures and
    sectors are plain-English labels intended for customer Market Map display.
    """

    event_family: str
    event_type: str
    status: str = "reported"

    title: str = ""
    summary_plain_english: str = ""

    affected_channels: list[str] = field(default_factory=list)

    likely_positive_exposures: list[str] = field(default_factory=list)
    likely_negative_exposures: list[str] = field(default_factory=list)

    sectors_positive: list[str] = field(default_factory=list)
    sectors_negative: list[str] = field(default_factory=list)

    themes_strengthened: list[str] = field(default_factory=list)
    themes_weakened: list[str] = field(default_factory=list)

    tickers_first_order: list[str] = field(default_factory=list)
    tickers_second_order: list[str] = field(default_factory=list)

    confirmation_signals: list[str] = field(default_factory=list)
    invalidation_signals: list[str] = field(default_factory=list)

    known_conflicts: list[str] = field(default_factory=list)

    entities: list[str] = field(default_factory=list)
    geography: list[str] = field(default_factory=list)

    source_confidence: str = "medium"   # low | medium | high
    materiality: str = "medium"         # low | medium | high

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has(text: str, patterns: list[str]) -> bool:
    return any(p in text for p in patterns)


def _has_all_of(text: str, patterns: list[str]) -> bool:
    return all(p in text for p in patterns)


# ---------------------------------------------------------------------------
# Pattern banks (lowercase; matched as substrings)
# ---------------------------------------------------------------------------

_DEESCALATION_TERMS = [
    "ceasefire", "cease-fire", "cease fire", "truce",
    "peace deal", "peace agreement", "peace talks", "path to peace",
    "iran deal", "sanctions lifted", "embargo lifted",
    "trade deal signed", "de-escalation", "deescalation",
    "diplomatic breakthrough", "hormuz reopen", "hormuz reopening",
    "war ends", "war ended", "conflict resolved", "deal could happen",
    "peace pricing",
]

_ESCALATION_TERMS = [
    "invasion", "invades", "missile strike", "airstrike", "air strike",
    "nuclear strike", "nuclear test", "war begins", "war breaks out",
    "tensions escalate", "conflict escalates", "embargo imposed",
    "blockade", "sanctions imposed", "sanctions expanded",
    "regime change", "coup", "assassination", "tanker route closure",
    "supply disruption", "attack",
]

_OIL_RISING_TERMS = ["surge", "surges", "jumps", "jump", "spike",
                      "rallies", "rises", "soars"]
_OIL_FALLING_TERMS = ["falls", "drops", "tumbles", "plunges",
                       "lower", "down"]
_OIL_SUPPLY_TERMS = ["supply disruption", "blockade", "outage",
                      "opec cut", "supply cut", "supply shock",
                      "tanker route closure", "tanker route", "pipeline outage"]
_OIL_UNWIND_TERMS = ["ceasefire", "peace", "de-escalation", "deescalation",
                      "iran deal", "hormuz reopen", "supply returns",
                      "talks resume", "deal could happen", "reopening hopes"]

_EARNINGS_BEAT_TERMS = ["beat", "beats", "smashes", "crushes", "blowout",
                         "tops expectations", "exceeds expectations"]
_EARNINGS_RAISE_TERMS = ["raises guidance", "raised guidance", "raises outlook",
                          "raised outlook", "lifts guidance"]
_EARNINGS_MISS_TERMS = ["miss", "misses", "guide down", "guidance cut",
                         "warns", "margin warning", "weak outlook", "guidance lowered"]
_EARNINGS_CONTEXT = ["earnings", "revenue", "results", "guidance",
                      "quarter", "q1", "q2", "q3", "q4", "eps"]
_EARNINGS_STOCK_FALLS = ["stock falls", "shares fall", "stock drops",
                          "shares drop", "stock down", "shares down",
                          "after-hours", "after hours", "high expectations",
                          "margin concern", "margin concerns", "margin pressure",
                          "disappoints market", "valuation concern"]

_ACQUISITION_TERMS = [
    "to be acquired", "acquisition agreement", "merger agreement",
    "definitive agreement", "agreed to be acquired", "agreed to acquire",
    "tender offer", "per share in cash", "takeover bid", "going private",
    "take-private", "management buyout", "acquires", "acquisition",
    "buys for $", "announces acquisition",
]

_CHINA_POLICY_TERMS = ["stimulus", "reserve ratio", "rrr cut",
                        "property rescue", "fiscal package",
                        "infrastructure package", "easing measures",
                        "policy package", "stimulus package"]

_INDIA_POLICY_TERMS = ["election", "election result", "reform", "modi",
                        "fdi", "foreign investment", "infrastructure spending",
                        "policy uncertainty", "reform momentum"]

_FED_EASE_TERMS = ["fed cuts", "fed rate cut", "rate cut", "ecb cuts",
                    "boe cuts", "rba cuts"]
_FED_HAWKISH_TERMS = ["hawkish", "warns inflation", "slower cuts",
                       "inflation remains", "inflation too high",
                       "inflation still high", "future cuts may be slower",
                       "fewer cuts ahead"]

_INFLATION_HOT_TERMS = ["cpi", "inflation", "ppi"]
_INFLATION_HOT_QUALIFIERS = ["hot", "hotter", "higher than expected",
                              "above estimate", "above expectations",
                              "jumps", "tops estimate", "tops expectations",
                              "comes in hotter"]

_WEAK_JOBS_TERMS = ["weak jobs", "nonfarm payrolls miss", "nfp miss",
                     "unemployment rises", "jobs miss"]
_BAD_NEWS_GOOD_NEWS_QUALIFIERS = ["yields lower", "yields drop",
                                    "stocks higher", "stocks rally",
                                    "rate cut odds rise", "rate cut hopes",
                                    "rate cuts", "more rate cuts"]

_CREDIT_STRESS_SUBJECTS = ["bank", "regional bank", "banks"]
_CREDIT_STRESS_TERMS = ["stress", "loss", "losses", "fall", "deposit pressure",
                         "credit losses", "deposit outflows", "funding pressure",
                         "shares fall", "deposits flee"]

_CHIP_TERMS = ["chip", "chips", "semiconductor", "semiconductors"]
_CHIP_RESTRICTION_TERMS = ["export restriction", "export ban", "export curb",
                            "china exposure", "advanced chip", "advanced ai chip",
                            "ai chip restriction", "ai chip export"]

_CYBER_TERMS = ["cyberattack", "cyber attack", "cyber-attack", "data breach",
                 "ransomware", "hacked"]
_COMPANY_SHOCK_TERMS = ["ceo exit", "ceo resigns", "ceo steps down",
                         "fraud investigation", "recall", "product recall"]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _b_geo_deescalation(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="geopolitics",
        event_type="de_escalation",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Geopolitical de-escalation: peace, ceasefire, or sanctions relief reported. "
            "Risk premium may fade across defence, oil and volatility; "
            "risk-on assets and consumer/transport names may benefit."
        ),
        affected_channels=[
            "geopolitical_risk", "risk_appetite",
            "commodity_prices", "sector_rotation", "volatility",
        ],
        likely_positive_exposures=[
            "broad risk appetite",
            "airlines and transport",
            "consumer-sensitive sectors",
            "small caps",
            "growth equities",
        ],
        likely_negative_exposures=[
            "oil risk premium",
            "energy producers",
            "defence premium",
            "implied volatility",
            "gold safe-haven bid",
        ],
        sectors_positive=["airlines", "consumer discretionary", "travel and leisure"],
        sectors_negative=["energy", "defence", "gold"],
        themes_strengthened=["risk_on_rotation", "travel_leisure", "consumer_discretionary"],
        themes_weakened=["defence_premium", "energy", "gold_safe_haven"],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Oil prices fall and stay lower",
            "Defence stocks weaken",
            "VIX declines",
            "Airline and transport shares rally",
        ],
        invalidation_signals=[
            "Headlines reverse — deal collapses or denied",
            "Renewed strikes or military action",
            "Oil rebounds sharply",
            "Defence stocks rally on follow-through risk",
        ],
        known_conflicts=[],
        geography=["middle east"] if "iran" in headline.lower() or "hormuz" in headline.lower() else [],
        source_confidence="medium",
        materiality="high",
    )


def _b_geo_escalation(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="geopolitics",
        event_type="escalation",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Geopolitical escalation: attack, sanctions, or conflict escalation reported. "
            "Risk premium typically widens — oil, defence, volatility and gold may bid; "
            "risk-on assets and consumer/transport may underperform."
        ),
        affected_channels=[
            "geopolitical_risk", "risk_appetite", "commodity_prices",
            "volatility", "sector_rotation", "supply_chain",
        ],
        likely_positive_exposures=[
            "oil risk premium",
            "energy producers",
            "defence",
            "implied volatility",
            "gold safe-haven bid",
        ],
        likely_negative_exposures=[
            "broad risk appetite",
            "airlines and transport",
            "consumer-sensitive sectors",
            "inflation-sensitive risk assets",
        ],
        sectors_positive=["energy", "defence", "gold"],
        sectors_negative=["airlines", "consumer discretionary", "travel and leisure"],
        themes_strengthened=["defence_premium", "energy", "gold_safe_haven"],
        themes_weakened=["risk_on_rotation", "travel_leisure"],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Oil and gold rally",
            "VIX rises",
            "Defence stocks lead",
            "Airlines and consumer discretionary lag",
        ],
        invalidation_signals=[
            "De-escalation headlines",
            "Oil and gold give back gains",
            "Risk appetite returns",
        ],
        known_conflicts=[],
        geography=["middle east"] if "iran" in headline.lower() or "hormuz" in headline.lower() else [],
        source_confidence="medium",
        materiality="high",
    )


def _b_oil_supply_shock(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="commodities",
        event_type="oil_supply_shock",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Oil supply shock reported. Higher oil prices typically support "
            "energy producers and volatility, while pressuring airlines, "
            "transport, consumer-sensitive sectors and inflation-sensitive risk assets."
        ),
        affected_channels=[
            "commodity_prices", "inflation_expectations",
            "risk_appetite", "supply_chain", "sector_rotation",
        ],
        likely_positive_exposures=[
            "oil",
            "energy producers",
            "implied volatility",
        ],
        likely_negative_exposures=[
            "airlines and transport",
            "consumer discretionary",
            "inflation-sensitive risk assets",
            "broad risk appetite if inflation pressure rises",
        ],
        sectors_positive=["energy"],
        sectors_negative=["airlines", "consumer discretionary", "travel and leisure"],
        themes_strengthened=["energy", "oil_supply_shock"],
        themes_weakened=["travel_leisure", "consumer_discretionary"],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Crude prices hold gains",
            "Energy sector outperforms",
            "Airlines and transport underperform",
        ],
        invalidation_signals=[
            "Supply concern resolved",
            "Oil retraces",
            "OPEC adds supply",
        ],
        known_conflicts=[],
        geography=[],
        source_confidence="medium",
        materiality="high",
    )


def _b_oil_risk_unwind(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="commodities",
        event_type="oil_risk_premium_unwind",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Oil is falling as traders may be pricing out geopolitical risk premium. "
            "This is different from saying there is no oil event — the premium itself "
            "is unwinding. Airlines, transport and consumer-sensitive sectors may benefit; "
            "energy and defence premium may fade."
        ),
        affected_channels=[
            "commodity_prices", "geopolitical_risk", "risk_appetite",
            "sector_rotation", "inflation_expectations",
        ],
        likely_positive_exposures=[
            "broad risk appetite",
            "airlines and transport",
            "consumer-sensitive sectors",
        ],
        likely_negative_exposures=[
            "oil risk premium",
            "energy producers",
            "defence premium",
            "implied volatility",
            "gold safe-haven bid",
        ],
        sectors_positive=["airlines", "consumer discretionary", "travel and leisure"],
        sectors_negative=["energy", "defence", "gold"],
        themes_strengthened=["risk_on_rotation", "travel_leisure"],
        themes_weakened=["energy", "defence_premium", "gold_safe_haven"],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Oil stays lower",
            "Airlines and transport rally",
            "VIX falls",
        ],
        invalidation_signals=[
            "Renewed supply or conflict headlines",
            "Oil retakes prior level",
        ],
        known_conflicts=[],
        geography=[],
        source_confidence="medium",
        materiality="high",
    )


def _b_earnings_positive(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="earnings_guidance",
        event_type="positive_surprise",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Company reported a positive earnings or guidance surprise. "
            "Likely supportive of the underlying name and related theme exposure."
        ),
        affected_channels=[
            "earnings_guidance", "revenue_growth",
            "valuation_multiple", "theme_acceleration",
        ],
        likely_positive_exposures=["underlying name", "sector peers"],
        likely_negative_exposures=[],
        sectors_positive=[],
        sectors_negative=[],
        themes_strengthened=[],
        themes_weakened=[],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=["Shares hold gains", "Analyst upgrades follow"],
        invalidation_signals=["Shares fade", "Guidance walk-back"],
        known_conflicts=[],
        geography=[],
        source_confidence="medium",
        materiality="medium",
    )


def _b_earnings_positive_conflict(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="earnings_guidance",
        event_type="positive_surprise_market_rejecting",
        status="under_review",
        title=headline[:200],
        summary_plain_english=(
            "Company beat earnings or raised guidance, but the market is "
            "questioning the result — margin concern, crowded expectations, "
            "or valuation. The underlying theme may remain supported even as the "
            "specific name is under review."
        ),
        affected_channels=[
            "earnings_guidance", "revenue_growth", "margins",
            "valuation_multiple", "theme_acceleration",
        ],
        likely_positive_exposures=[
            "underlying demand trend",
            "theme exposure",
        ],
        likely_negative_exposures=[
            "margins",
            "valuation multiple",
            "crowded positioning",
            "near-term stock price",
        ],
        sectors_positive=[],
        sectors_negative=[],
        themes_strengthened=[],
        themes_weakened=[],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Shares recover after the initial drop",
            "Analysts defend on demand strength",
        ],
        invalidation_signals=[
            "Continued downgrades on margin concerns",
            "Theme peers also sold",
        ],
        known_conflicts=[
            "Headline read is positive (beat + raise) but market reaction is negative.",
        ],
        geography=[],
        source_confidence="high",
        materiality="high",
    )


def _b_earnings_negative(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="earnings_guidance",
        event_type="negative_surprise",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Company missed earnings or cut guidance. Pressure on the name "
            "and possibly on sector peers."
        ),
        affected_channels=[
            "earnings_guidance", "revenue_growth", "margins",
            "valuation_multiple", "theme_reversal",
        ],
        likely_positive_exposures=[],
        likely_negative_exposures=["underlying name", "sector peers"],
        sectors_positive=[],
        sectors_negative=[],
        themes_strengthened=[],
        themes_weakened=[],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=["Shares lower", "Analyst downgrades"],
        invalidation_signals=["Shares recover", "Company reiterates outlook"],
        known_conflicts=[],
        geography=[],
        source_confidence="medium",
        materiality="medium",
    )


def _b_acquisition(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="corporate_action",
        event_type="acquisition",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Corporate acquisition or merger announced. Typically lifts the "
            "target and pressures the acquirer near-term; peers may re-rate "
            "on M&A read-through; regulatory scrutiny is a known risk."
        ),
        affected_channels=[
            "strategic_positioning" if False else "valuation_multiple",
            "regulation", "sector_rotation",
        ],
        likely_positive_exposures=[
            "acquisition target",
            "sector peers (M&A read-through)",
        ],
        likely_negative_exposures=[
            "acquirer near-term (deal funding, integration risk)",
        ],
        sectors_positive=[],
        sectors_negative=[],
        themes_strengthened=[],
        themes_weakened=[],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Both parties confirm terms",
            "Peer multiples re-rate higher",
        ],
        invalidation_signals=[
            "Deal breaks",
            "Regulatory block announced",
        ],
        known_conflicts=[],
        geography=[],
        source_confidence="high",
        materiality="high",
    )


def _b_china_stimulus(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="major_economy_policy",
        event_type="china_stimulus",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "China policy stimulus announced. Supports global growth expectations, "
            "industrial commodities, and EM/China-exposed equities."
        ),
        affected_channels=[
            "growth_expectations", "commodity_prices",
            "risk_appetite", "sector_rotation",
        ],
        likely_positive_exposures=[
            "industrial commodities (copper, iron ore)",
            "China-exposed equities",
            "EM equities",
            "global cyclicals",
        ],
        likely_negative_exposures=[
            "safe-haven assets if risk-on broadens",
        ],
        sectors_positive=["industrials", "materials", "mining"],
        sectors_negative=[],
        themes_strengthened=["china_reopening", "industrial_metals"],
        themes_weakened=[],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Copper, iron ore higher",
            "China ETFs outperform",
            "Industrial sector leads",
        ],
        invalidation_signals=[
            "Stimulus underwhelms in detail",
            "Property sector still distressed",
        ],
        known_conflicts=[],
        geography=["china"],
        source_confidence="medium",
        materiality="high",
    )


def _b_india_policy(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="major_economy_policy",
        event_type="india_policy_event",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "India policy or election development reported. Implications for "
            "reform continuity, infrastructure spending, foreign investment "
            "flows and EM positioning."
        ),
        affected_channels=[
            "growth_expectations", "risk_appetite",
            "sector_rotation", "currency", "regulation",
        ],
        likely_positive_exposures=[
            "Indian equities if reform momentum confirmed",
            "Infrastructure / capex exposure",
        ],
        likely_negative_exposures=[
            "Indian equities if policy uncertainty rises",
            "FDI-sensitive names",
        ],
        sectors_positive=["infrastructure", "capital goods"],
        sectors_negative=[],
        themes_strengthened=["india_reform"],
        themes_weakened=[],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Indian indices hold gains",
            "Rupee stable",
            "FDI flows continue",
        ],
        invalidation_signals=[
            "Political deadlock",
            "Rupee weakness",
            "FDI flows reverse",
        ],
        known_conflicts=[],
        geography=["india"],
        source_confidence="medium",
        materiality="high",
    )


def _b_central_bank_conflict(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="central_bank",
        event_type="rate_cut_with_hawkish_guidance",
        status="under_review",
        title=headline[:200],
        summary_plain_english=(
            "Central bank cut rates but signalled inflation concern or a slower "
            "future cut path. Headline policy easing is supportive, but the rate "
            "path may reset higher — long-duration growth assets are caught "
            "between two opposing signals."
        ),
        affected_channels=[
            "interest_rates", "valuation_multiple", "currency",
            "growth_expectations", "risk_appetite",
        ],
        likely_positive_exposures=[
            "headline policy easing",
        ],
        likely_negative_exposures=[
            "future rate path",
            "long-duration growth equities",
            "rate-sensitive bonds",
            "weaker domestic currency",
        ],
        sectors_positive=[],
        sectors_negative=[],
        themes_strengthened=[],
        themes_weakened=[],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Yields move higher despite the cut",
            "Long-duration growth underperforms",
        ],
        invalidation_signals=[
            "Yields fall as the market reads through the rhetoric",
            "Growth equities rally",
        ],
        known_conflicts=[
            "Headline (cut) is dovish but guidance is hawkish — direction is conflicting.",
        ],
        geography=[],
        source_confidence="high",
        materiality="high",
    )


def _b_central_bank_easing(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="central_bank",
        event_type="rate_cut",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Central bank cut rates. Typically supportive for risk assets and "
            "long-duration growth, pressures the currency."
        ),
        affected_channels=[
            "interest_rates", "valuation_multiple",
            "growth_expectations", "risk_appetite", "currency",
        ],
        likely_positive_exposures=[
            "long-duration growth equities",
            "REITs and rate-sensitive bonds",
            "broad risk appetite",
        ],
        likely_negative_exposures=[
            "domestic currency",
        ],
        sectors_positive=["technology", "growth", "reits"],
        sectors_negative=[],
        themes_strengthened=["yields_falling", "risk_on_rotation"],
        themes_weakened=["yields_rising"],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=["Yields fall", "Growth leads"],
        invalidation_signals=["Yields rise on inflation concern"],
        known_conflicts=[],
        geography=[],
        source_confidence="high",
        materiality="high",
    )


def _b_hot_inflation(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="macro_data",
        event_type="hot_inflation_print",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Inflation came in hotter than expected. Yields jump, rate cut "
            "expectations fall back. Pressures valuation multiples and rate-"
            "sensitive assets."
        ),
        affected_channels=[
            "inflation_expectations", "interest_rates",
            "valuation_multiple", "risk_appetite",
        ],
        likely_positive_exposures=[
            "inflation-protected assets",
            "energy and commodities",
            "financials (curve steepening)",
        ],
        likely_negative_exposures=[
            "long-duration growth equities",
            "rate-sensitive bonds",
            "REITs",
            "broad risk appetite near-term",
        ],
        sectors_positive=["energy", "financials"],
        sectors_negative=["technology", "growth", "reits"],
        themes_strengthened=["yields_rising"],
        themes_weakened=["yields_falling", "risk_on_rotation"],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=["Yields hold higher", "Growth lags"],
        invalidation_signals=["Yields retrace", "Soft follow-on data"],
        known_conflicts=[],
        geography=[],
        source_confidence="high",
        materiality="high",
    )


def _b_bad_news_good_news(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="macro_data",
        event_type="weak_data_rate_cut_rally",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Weak macro data is being read by the market as supportive of rate "
            "cuts. Yields are falling and equities are rallying — bad-news-good-news "
            "interpretation. This should not be read as automatically bearish."
        ),
        affected_channels=[
            "interest_rates", "growth_expectations",
            "valuation_multiple", "risk_appetite",
        ],
        likely_positive_exposures=[
            "long-duration growth equities",
            "rate-sensitive bonds",
            "broad risk appetite (short-term)",
        ],
        likely_negative_exposures=[
            "domestic currency",
            "cyclicals if growth weakens further",
        ],
        sectors_positive=["technology", "growth", "reits"],
        sectors_negative=[],
        themes_strengthened=["yields_falling", "risk_on_rotation"],
        themes_weakened=["yields_rising"],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Yields hold lower",
            "Rate cut probability rises",
            "Growth leads",
        ],
        invalidation_signals=[
            "Hot follow-on data resets rate path",
            "Growth concern outweighs cut hopes",
        ],
        known_conflicts=[
            "Data is weak (negative) but market reaction is positive — read carefully.",
        ],
        geography=[],
        source_confidence="medium",
        materiality="medium",
    )


def _b_credit_stress(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="credit_liquidity",
        event_type="bank_or_credit_stress",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Bank or credit stress reported — deposit pressure, credit losses "
            "or funding strain. Typically tightens financial conditions and "
            "weighs on risk appetite."
        ),
        affected_channels=[
            "credit_stress", "risk_appetite", "liquidity",
        ],
        likely_positive_exposures=[
            "safe-haven assets",
            "treasuries",
        ],
        likely_negative_exposures=[
            "regional banks",
            "credit-sensitive equities",
            "broad risk appetite",
        ],
        sectors_positive=["treasuries", "gold"],
        sectors_negative=["regional banks", "financials"],
        themes_strengthened=["credit_stress_rising"],
        themes_weakened=["credit_stress_easing", "risk_on_rotation"],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "High-yield spreads widen",
            "Bank shares fall further",
        ],
        invalidation_signals=[
            "Spreads tighten",
            "Bank shares recover on regulatory backstop",
        ],
        known_conflicts=[],
        geography=[],
        source_confidence="medium",
        materiality="high",
    )


def _b_chip_export_restriction(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="regulation_legal",
        event_type="chip_export_restriction",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Government announced new restrictions on advanced chip exports. "
            "Negative for chip names with China exposure; can pressure the AI "
            "supply chain but also accelerate reshoring and domestic "
            "semiconductor capacity themes."
        ),
        affected_channels=[
            "regulation", "supply_chain", "revenue_growth",
            "geopolitical_risk", "sector_rotation",
        ],
        likely_positive_exposures=[
            "domestic semiconductor capacity",
            "reshoring beneficiaries",
        ],
        likely_negative_exposures=[
            "semiconductor names with China revenue",
            "AI supply chain near-term",
            "China tech exposure",
        ],
        sectors_positive=["domestic semiconductor capacity"],
        sectors_negative=["semiconductors", "ai supply chain"],
        themes_strengthened=["semiconductor_reshoring"],
        themes_weakened=["ai_compute_infrastructure"],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "China-exposed chip names underperform",
            "Reshoring beneficiaries rally",
        ],
        invalidation_signals=[
            "Restrictions diluted in detail",
            "Carve-outs granted",
        ],
        known_conflicts=[],
        geography=["united states", "china"],
        source_confidence="high",
        materiality="high",
    )


def _b_cyberattack(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="company_specific_shock",
        event_type="cyberattack",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Cyberattack or data breach reported. Negative for the affected "
            "company; can be a positive read-through for cybersecurity demand."
        ),
        affected_channels=[
            "revenue_growth", "regulation",
            "supply_chain", "theme_acceleration",
        ],
        likely_positive_exposures=[
            "cybersecurity demand",
        ],
        likely_negative_exposures=[
            "victim company",
            "consumer trust where applicable",
        ],
        sectors_positive=["cybersecurity"],
        sectors_negative=[],
        themes_strengthened=["cybersecurity"],
        themes_weakened=[],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=[
            "Cybersecurity stocks rally",
            "Victim discloses material impact",
        ],
        invalidation_signals=[
            "Disclosed scope is minor",
            "Operations restored quickly",
        ],
        known_conflicts=[],
        geography=[],
        source_confidence="medium",
        materiality="medium",
    )


def _b_company_shock(headline: str, symbols: list[str]) -> ClassifiedEvent:
    return ClassifiedEvent(
        event_family="company_specific_shock",
        event_type="leadership_or_legal_shock",
        status="reported",
        title=headline[:200],
        summary_plain_english=(
            "Company-specific shock: CEO exit, recall, or fraud investigation. "
            "Typically negative for the underlying name and possibly peers."
        ),
        affected_channels=[
            "regulation", "revenue_growth", "valuation_multiple",
        ],
        likely_positive_exposures=[],
        likely_negative_exposures=[
            "underlying name",
            "sector peers if systemic",
        ],
        sectors_positive=[],
        sectors_negative=[],
        themes_strengthened=[],
        themes_weakened=[],
        tickers_first_order=symbols,
        tickers_second_order=[],
        confirmation_signals=["Analyst downgrades", "Regulatory inquiry"],
        invalidation_signals=["Quick replacement", "Issue contained"],
        known_conflicts=[],
        geography=[],
        source_confidence="medium",
        materiality="medium",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def classify_headline(
    headline: str,
    snippet: str = "",
    symbols: list[str] | None = None,
) -> list[ClassifiedEvent]:
    """Classify a headline into zero or more customer-safe events.

    Pure function. Deterministic. No I/O. No LLM. Safe to call from any layer.
    """
    if not headline or not isinstance(headline, str):
        return []

    text = f"{headline} {snippet}".lower()
    symbols = symbols or []
    out: list[ClassifiedEvent] = []

    is_deescalation = _has(text, _DEESCALATION_TERMS)
    is_escalation = _has(text, _ESCALATION_TERMS)

    # Geopolitics: de-escalation
    if is_deescalation and not is_escalation:
        out.append(_b_geo_deescalation(headline, symbols))

    # Geopolitics: escalation
    if is_escalation and not is_deescalation:
        out.append(_b_geo_escalation(headline, symbols))

    # Commodities: oil supply shock OR risk-premium unwind
    if "oil" in text or "crude" in text or "brent" in text or "wti" in text:
        oil_rising = _has(text, _OIL_RISING_TERMS)
        oil_falling = _has(text, _OIL_FALLING_TERMS)
        has_supply_term = _has(text, _OIL_SUPPLY_TERMS)
        has_unwind_term = _has(text, _OIL_UNWIND_TERMS) or is_deescalation

        if oil_rising and has_supply_term:
            out.append(_b_oil_supply_shock(headline, symbols))
        elif oil_falling and has_unwind_term:
            out.append(_b_oil_risk_unwind(headline, symbols))

    # Earnings — beat or raise
    has_beat = _has(text, _EARNINGS_BEAT_TERMS)
    has_raise = _has(text, _EARNINGS_RAISE_TERMS)
    has_miss = _has(text, _EARNINGS_MISS_TERMS)
    has_earnings_ctx = _has(text, _EARNINGS_CONTEXT)
    has_stock_falls = _has(text, _EARNINGS_STOCK_FALLS)

    if (has_beat or has_raise) and has_earnings_ctx and not has_miss:
        if has_stock_falls:
            out.append(_b_earnings_positive_conflict(headline, symbols))
        else:
            out.append(_b_earnings_positive(headline, symbols))
    elif has_miss and has_earnings_ctx and not (has_beat or has_raise):
        out.append(_b_earnings_negative(headline, symbols))

    # M&A / acquisition
    if _has(text, _ACQUISITION_TERMS):
        out.append(_b_acquisition(headline, symbols))

    # China stimulus
    if "china" in text and _has(text, _CHINA_POLICY_TERMS):
        out.append(_b_china_stimulus(headline, symbols))

    # India policy
    if "india" in text and _has(text, _INDIA_POLICY_TERMS):
        out.append(_b_india_policy(headline, symbols))

    # Central bank — easing vs. conflicting
    if _has(text, _FED_EASE_TERMS):
        if _has(text, _FED_HAWKISH_TERMS):
            out.append(_b_central_bank_conflict(headline, symbols))
        else:
            out.append(_b_central_bank_easing(headline, symbols))

    # Hot inflation print
    if _has(text, _INFLATION_HOT_TERMS) and _has(text, _INFLATION_HOT_QUALIFIERS):
        out.append(_b_hot_inflation(headline, symbols))

    # Bad-news-good-news (weak data, market rallies)
    if _has(text, _WEAK_JOBS_TERMS) and _has(text, _BAD_NEWS_GOOD_NEWS_QUALIFIERS):
        out.append(_b_bad_news_good_news(headline, symbols))

    # Credit / bank stress
    if _has(text, _CREDIT_STRESS_SUBJECTS) and _has(text, _CREDIT_STRESS_TERMS):
        out.append(_b_credit_stress(headline, symbols))

    # Chip export restriction
    if _has(text, _CHIP_TERMS) and _has(text, _CHIP_RESTRICTION_TERMS):
        out.append(_b_chip_export_restriction(headline, symbols))

    # Cyberattack
    if _has(text, _CYBER_TERMS):
        out.append(_b_cyberattack(headline, symbols))

    # Company-specific shock (only if no other family fired)
    if not out and _has(text, _COMPANY_SHOCK_TERMS):
        out.append(_b_company_shock(headline, symbols))

    return out
