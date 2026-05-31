# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  macro_event_layer.py                      ║
# ║   Macro Event Intelligence Layer                             ║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
macro_event_layer.py — Macro event ingestion, classification, and store.

Single responsibility: ingest news headlines → determine if macro-significant
via LLM → store structured macro events that the driver resolver can use to
back price signals with real event context.

Architecture position:
  News pipeline (news.py, alpaca_news.py)
      → macro_event_layer.maybe_record_macro_event()   [fail-soft gate]
          → keyword pre-filter (cheap, extensive)
              → LLM classification (Sonnet — only when keyword gate passes)
                  → macro_events.jsonl

Consumed by:
  live_driver_resolver.py  — annotates driver state with event backing
  intelligence_api.py      — GET /api/intelligence/macro-events
  Ask Decifer              — via intelligence API

Boundaries (hard rules):
  - ONLY fed from news. No other input source.
  - No imports from execution, orders, broker, PM, universe scoring modules.
  - Does not activate, modify, or block drivers. Read by live_driver_resolver;
    never writes to live_driver_state.json itself.
  - Does not affect trade execution, PM actions, or handoff eligibility.
  - LLM is used ONLY for classification, never for trading decisions.

Public surface:
    maybe_record_macro_event(headline, snippet, source, published_at) -> str | None
        Fail-soft. Returns event_id if recorded, None otherwise.

    get_recent_events(within_hours) -> list[dict]
    get_events_for_driver(driver_id) -> list[dict]
    get_active_context() -> dict   — structured summary for driver resolver
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("decifer.macro_event_layer")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BASE = os.path.dirname(os.path.abspath(__file__))
_STORE_PATH = os.path.join(_BASE, "data/intelligence/macro_events.jsonl")
_LOCK = threading.Lock()

_SCHEMA_VERSION = "macro_event_v1"

# ---------------------------------------------------------------------------
# Rate-limit: max Sonnet calls per hour to avoid runaway spend
# ---------------------------------------------------------------------------

_MAX_LLM_CALLS_PER_HOUR = 30
_llm_call_timestamps: list[float] = []
_llm_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Event type → TTL hours
# ---------------------------------------------------------------------------

_TTL_BY_EVENT_TYPE: dict[str, float] = {
    # Geopolitical
    "military_conflict":            336.0,  # 14 days — active until contradicted
    "geopolitical_escalation":      336.0,
    "geopolitical_de_escalation":   168.0,
    "ceasefire_negotiation":        168.0,
    "regime_change":                336.0,
    "coup_or_uprising":             336.0,
    "assassination_or_attack":      168.0,
    "territorial_dispute":          336.0,
    "hostage_or_detainee":          168.0,
    # Sanctions / trade
    "sanctions_imposed":            336.0,
    "sanctions_lifted":             168.0,
    "trade_restriction":            336.0,
    "tariff_announced":             336.0,
    "trade_agreement":              168.0,
    "export_ban":                   336.0,
    "import_restriction":           336.0,
    "decoupling_policy":            336.0,
    # Central bank / monetary
    "central_bank_rate_decision":   168.0,  # 7 days
    "central_bank_forward_guidance":168.0,
    "quantitative_easing":          336.0,
    "quantitative_tightening":      336.0,
    "emergency_monetary_action":    168.0,
    "currency_intervention":        168.0,
    # Macro data
    "inflation_data":                48.0,
    "employment_data":               48.0,
    "gdp_data":                      72.0,
    "pmi_data":                      48.0,
    "retail_sales_data":             48.0,
    "housing_data":                  48.0,
    "trade_balance_data":            48.0,
    "consumer_confidence_data":      48.0,
    "manufacturing_data":            48.0,
    # Supply / commodities
    "oil_supply_shock":             168.0,
    "oil_demand_shift":             168.0,
    "opec_decision":                168.0,
    "infrastructure_disruption":    168.0,  # strait, pipeline, port
    "supply_chain_disruption":      168.0,
    "commodity_shock":              168.0,
    "energy_price_shock":           168.0,
    "food_supply_shock":            168.0,
    "semiconductor_shortage":       336.0,
    "shipping_disruption":          168.0,
    # Fiscal / government
    "fiscal_policy":                168.0,
    "debt_ceiling":                 168.0,
    "government_shutdown":          168.0,
    "stimulus_announcement":        168.0,
    "budget_crisis":                168.0,
    "sovereign_default_risk":       336.0,
    "election_outcome":             168.0,
    "political_transition":         336.0,
    "regulatory_action":            168.0,
    "nationalisation":              336.0,
    # Credit / banking
    "banking_stress":               168.0,
    "bank_failure":                 336.0,
    "credit_event":                 168.0,
    "sovereign_downgrade":          168.0,
    "financial_contagion":          168.0,
    "imf_intervention":             168.0,
    "liquidity_crisis":             168.0,
    # Currency / FX
    "currency_crisis":              168.0,
    "capital_controls":             336.0,
    "devaluation":                  168.0,
    "dollar_policy_shift":          168.0,
    # Natural / force majeure
    "natural_disaster":              72.0,
    "pandemic_outbreak":            336.0,
    "disease_spread":               168.0,
    "nuclear_incident":             336.0,
    "climate_event":                 72.0,
    # Technology / systemic
    "technology_export_control":    336.0,
    "critical_infrastructure_attack":168.0,
    "cyber_attack_systemic":        168.0,
    # Default
    "other_macro":                   72.0,
}

_DEFAULT_TTL_HOURS = 72.0

# ---------------------------------------------------------------------------
# Known drivers — must match live_driver_resolver.py
# ---------------------------------------------------------------------------

_KNOWN_DRIVERS: frozenset[str] = frozenset({
    "ai_capex_growth",
    "ai_compute_demand",
    "yields_rising",
    "yields_falling",
    "oil_supply_shock",
    "geopolitical_risk_rising",
    "geopolitical_risk_falling",
    "credit_stress_rising",
    "credit_stress_easing",
    "risk_off_rotation",
    "risk_on_rotation",
    "gold_safe_haven_bid",
    "small_cap_risk_on",
    "futures_risk_on",
    "futures_risk_off",
})

# ---------------------------------------------------------------------------
# Extensive keyword pre-filter
# Broad by design — we want false positives here, not false negatives.
# The LLM is the real gate. This just prevents Sonnet from seeing earnings
# beats, product launches, and routine analyst upgrades.
# ---------------------------------------------------------------------------

_KEYWORD_GROUPS: dict[str, list[str]] = {
    "geopolitics_conflict": [
        "war", "conflict", "military", "strike", "airstrike", "missile",
        "bomb", "explosion", "troops", "invasion", "offensive", "ceasefire",
        "attack", "drone", "nuclear", "weapon", "armed", "soldier", "navy",
        "air force", "ground forces", "frontline", "siege", "occupation",
        "territorial", "sovereignty", "annexation", "blockade", "fleet",
        "carrier", "fighter jet", "intercept", "escalation", "de-escalation",
        "hostilities", "hostage", "captive", "prisoner", "assassination",
        "coup", "uprising", "revolution", "protest", "crackdown", "riot",
        "regime", "insurgency", "guerrilla", "terrorist", "extremist",
        "jihad", "militant", "rebel", "proxy", "paramilitary",
    ],
    "geopolitics_diplomacy": [
        "negotiation", "peace talks", "summit", "diplomatic", "agreement",
        "treaty", "alliance", "nato", "un security council", "united nations",
        "g7", "g20", "bilateral", "multilateral", "embassy", "ambassador",
        "sanctions", "expel", "expulsion", "visa ban", "travel ban",
        "asset freeze", "blacklist", "designat", "iaea", "icc",
    ],
    "geography_hotspots": [
        "iran", "israel", "ukraine", "russia", "taiwan", "china", "beijing",
        "north korea", "pyongyang", "syria", "iran", "gaza", "west bank",
        "lebanon", "hezbollah", "hamas", "houthi", "red sea", "hormuz",
        "strait of hormuz", "persian gulf", "south china sea", "taiwan strait",
        "black sea", "suez", "panama canal", "bab el-mandeb", "oman",
        "saudi arabia", "riyadh", "iran", "tehran", "moscow", "kyiv",
        "kabul", "baghdad", "tripoli", "venezuela", "caracas", "myanmar",
        "pakistan", "india", "kashmir", "tibet", "xinjiang", "hong kong",
        "irgc", "revolutionary guard", "kremlin", "pentagon", "nato",
    ],
    "central_bank_monetary": [
        "federal reserve", "fed", "fomc", "rate decision", "rate hike",
        "rate cut", "interest rate", "monetary policy", "inflation target",
        "quantitative easing", "quantitative tightening", "qe", "qt",
        "balance sheet", "tapering", "forward guidance", "dovish", "hawkish",
        "powell", "lagarde", "ecb", "boj", "bank of japan", "bank of england",
        "pboc", "peoples bank", "rba", "boe", "bnp", "snb", "riksbank",
        "basis points", "bps", "fed funds rate", "overnight rate",
        "emergency cut", "emergency hike", "yield curve control",
        "repo rate", "discount rate", "reserve requirement",
    ],
    "macro_data_releases": [
        "cpi", "pce", "inflation", "core inflation", "deflation",
        "nfp", "non-farm payroll", "unemployment rate", "jobless",
        "initial claims", "jobs report", "employment", "labor market",
        "gdp", "gross domestic product", "recession", "contraction",
        "pmi", "purchasing managers", "ism", "manufacturing index",
        "retail sales", "consumer spending", "consumer confidence",
        "housing starts", "existing home", "building permits",
        "trade deficit", "current account", "balance of payments",
        "fiscal deficit", "debt-to-gdp", "budget surplus", "budget deficit",
        "industrial production", "capacity utilization",
    ],
    "oil_energy_commodities": [
        "oil", "crude", "brent", "wti", "opec", "opec+", "saudi aramco",
        "production cut", "production increase", "output quota",
        "pipeline", "refinery", "tanker", "supertanker", "vlcc",
        "lng", "natural gas", "gas price", "coal", "energy price",
        "oil embargo", "oil ban", "energy sanction", "oil shock",
        "strategic reserve", "spr", "oil inventory", "eia report",
        "cushing", "energy crisis", "power outage", "grid failure",
        "uranium", "nuclear energy", "reactor", "enrichment",
        "wheat", "grain", "corn", "soybean", "food price", "famine",
        "drought", "harvest", "crop", "commodity", "copper", "lithium",
        "rare earth", "critical mineral", "gold", "silver", "metals",
    ],
    "supply_chain_trade": [
        "supply chain", "semiconductor", "chip", "chipmaker", "tsmc",
        "shortage", "bottleneck", "inventory", "stockpile", "backlog",
        "tariff", "trade war", "trade deal", "import ban", "export ban",
        "export control", "commerce department", "entity list",
        "wto", "free trade", "protectionism", "reshoring", "nearshoring",
        "decoupling", "friend-shoring", "supply disruption",
        "shipping", "freight", "container", "port", "dock", "logistics",
        "rail disruption", "canal", "route closure", "chokepoint",
    ],
    "fiscal_government": [
        "debt ceiling", "government shutdown", "continuing resolution",
        "stimulus", "fiscal package", "spending bill", "appropriations",
        "tax reform", "tax cut", "tax hike", "budget", "deficit",
        "austerity", "sovereign debt", "default", "restructuring",
        "imf", "world bank", "bailout", "rescue package",
        "election", "vote", "referendum", "inauguration",
        "executive order", "regulation", "deregulation", "ban",
        "antitrust", "nationalisation", "privatisation", "expropriation",
    ],
    "banking_credit": [
        "bank failure", "bank run", "deposit flight", "bank rescue",
        "fdic", "insolvency", "receivership", "bankruptcy", "chapter 11",
        "credit crunch", "credit event", "default", "debt restructuring",
        "sovereign downgrade", "rating downgrade", "moody", "s&p", "fitch",
        "junk", "high yield spread", "investment grade", "credit default swap",
        "cds", "contagion", "systemic risk", "too big to fail",
        "capital requirements", "stress test", "basel",
        "liquidity crisis", "repo market", "overnight funding",
        "financial crisis", "market crash", "circuit breaker",
    ],
    "currency_fx": [
        "currency crisis", "devaluation", "revaluation", "peg",
        "capital controls", "dollar", "yuan", "renminbi", "yen", "euro",
        "pound", "emerging market currency", "fx intervention",
        "reserve currency", "dedollarization", "swift ban",
        "cross-border payment", "bitcoin", "crypto", "digital currency",
        "cbdc", "stablecoin", "dollar weaponization",
    ],
    "natural_force_majeure": [
        "earthquake", "tsunami", "hurricane", "typhoon", "cyclone",
        "flood", "drought", "wildfire", "volcano", "landslide",
        "pandemic", "outbreak", "epidemic", "virus", "pathogen",
        "quarantine", "lockdown", "public health emergency",
        "nuclear accident", "chemical spill", "explosion", "infrastructure collapse",
    ],
    "technology_systemic": [
        "export control", "chip ban", "technology restriction",
        "huawei", "tiktok", "bytedance", "tech war",
        "cyber attack", "ransomware", "critical infrastructure hack",
        "grid attack", "water system", "satellite", "space weapon",
        "ai regulation", "ai ban", "ai legislation",
    ],
}

# Flatten to a single set for O(1) lookup after lowercasing
_ALL_KEYWORDS: frozenset[str] = frozenset(
    kw for group in _KEYWORD_GROUPS.values() for kw in group
)


def _passes_keyword_gate(headline: str, snippet: str = "") -> bool:
    """Return True if headline or snippet contains at least one macro keyword."""
    text = (headline + " " + snippet).lower()
    return any(kw in text for kw in _ALL_KEYWORDS)


# ---------------------------------------------------------------------------
# Deduplication — skip if a near-identical headline was classified recently
# ---------------------------------------------------------------------------

_recent_hashes: dict[str, float] = {}  # hash → epoch timestamp
_DEDUP_WINDOW_SECONDS = 3600.0  # 1 hour


def _headline_hash(headline: str) -> str:
    normalized = " ".join(headline.lower().split())
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


def _is_duplicate(headline: str) -> bool:
    import time
    h = _headline_hash(headline)
    now = time.time()
    cutoff = now - _DEDUP_WINDOW_SECONDS
    # Prune old entries
    for k in list(_recent_hashes.keys()):
        if _recent_hashes[k] < cutoff:
            del _recent_hashes[k]
    if h in _recent_hashes:
        return True
    _recent_hashes[h] = now
    return False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def _llm_call_allowed() -> bool:
    """Return True if we're under the per-hour LLM call cap."""
    import time
    now = time.time()
    cutoff = now - 3600.0
    with _llm_lock:
        # Prune timestamps older than 1 hour
        while _llm_call_timestamps and _llm_call_timestamps[0] < cutoff:
            _llm_call_timestamps.pop(0)
        if len(_llm_call_timestamps) >= _MAX_LLM_CALLS_PER_HOUR:
            return False
        _llm_call_timestamps.append(now)
        return True


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------

_CLASSIFICATION_PROMPT = """\
You are a macro event classifier for a trading intelligence system.

A news headline and optional snippet are provided. Your job:

1. Decide if this is a MACRO-SIGNIFICANT event — one that could move markets,
   affect asset classes, or change the macro environment. Company earnings,
   product launches, analyst upgrades, and routine corporate news are NOT
   macro-significant.

2. If macro-significant: classify it with full structured detail.

Respond with ONLY valid JSON matching this exact schema:

{
  "macro_significant": true | false,
  "event_type": "<one from the taxonomy below, or 'other_macro'>",
  "event_summary": "<1-2 sentence plain-English summary of what happened and why it matters>",
  "direction_of_risk": "risk_on" | "risk_off" | "mixed" | "neutral",
  "drivers_implicated": ["<driver_id>", ...],
  "theme_impacts": [
    {
      "theme": "<theme name>",
      "direction": "tailwind" | "headwind" | "neutral",
      "confidence": 0.0-1.0,
      "reasoning": "<one sentence>"
    }
  ],
  "affected_domains": ["oil", "credit", "equities", "bonds", "currency", "supply_chain",
                       "defence", "energy", "food", "semiconductors", "real_estate",
                       "gold", "emerging_markets", "crypto", "banking", "commodities"],
  "price_confirmation_signals": ["<what to watch in price action to confirm this thesis>"],
  "ttl_hours": <integer>,
  "confidence": 0.0-1.0
}

EVENT TYPE TAXONOMY (use exactly these strings):
military_conflict, geopolitical_escalation, geopolitical_de_escalation,
ceasefire_negotiation, regime_change, coup_or_uprising, assassination_or_attack,
territorial_dispute, hostage_or_detainee, sanctions_imposed, sanctions_lifted,
trade_restriction, tariff_announced, trade_agreement, export_ban, import_restriction,
decoupling_policy, central_bank_rate_decision, central_bank_forward_guidance,
quantitative_easing, quantitative_tightening, emergency_monetary_action,
currency_intervention, inflation_data, employment_data, gdp_data, pmi_data,
retail_sales_data, housing_data, trade_balance_data, consumer_confidence_data,
manufacturing_data, oil_supply_shock, oil_demand_shift, opec_decision,
infrastructure_disruption, supply_chain_disruption, commodity_shock,
energy_price_shock, food_supply_shock, semiconductor_shortage, shipping_disruption,
fiscal_policy, debt_ceiling, government_shutdown, stimulus_announcement, budget_crisis,
sovereign_default_risk, election_outcome, political_transition, regulatory_action,
nationalisation, banking_stress, bank_failure, credit_event, sovereign_downgrade,
financial_contagion, imf_intervention, liquidity_crisis, currency_crisis,
capital_controls, devaluation, dollar_policy_shift, natural_disaster,
pandemic_outbreak, disease_spread, nuclear_incident, climate_event,
technology_export_control, critical_infrastructure_attack, cyber_attack_systemic,
other_macro

KNOWN DRIVERS (only use these exact strings in drivers_implicated):
ai_capex_growth, ai_compute_demand, yields_rising, yields_falling,
oil_supply_shock, geopolitical_risk_rising, geopolitical_risk_falling,
credit_stress_rising, credit_stress_easing, risk_off_rotation, risk_on_rotation,
gold_safe_haven_bid, small_cap_risk_on

TTL guidance (use your judgment, override if context warrants):
- Active military conflict, sanctions, trade restriction: 336
- Central bank decision, supply shock, trade agreement: 168
- Macro data release (CPI, NFP, GDP, PMI): 48
- Natural disaster, credit event: 72
- Other: 72

Headline: {headline}
Snippet: {snippet}
"""


def _classify_with_llm(headline: str, snippet: str) -> dict | None:
    """Call Sonnet to classify the macro event. Returns parsed dict or None."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = _CLASSIFICATION_PROMPT.format(
            headline=headline,
            snippet=(snippet or "")[:500],
        )
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        if not isinstance(result.get("macro_significant"), bool):
            return None
        return result
    except Exception as exc:
        log.debug("macro_event_layer: LLM classification failed — %s", exc)
        return None


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _expires_at(event_type: str, ttl_hours_override: float | None = None) -> str:
    ttl = ttl_hours_override or _TTL_BY_EVENT_TYPE.get(event_type, _DEFAULT_TTL_HOURS)
    return (datetime.now(UTC) + timedelta(hours=ttl)).isoformat()


def _load_events() -> list[dict]:
    """Load all non-expired events from the store."""
    if not os.path.exists(_STORE_PATH):
        return []
    now = datetime.now(UTC)
    events: list[dict] = []
    try:
        with open(_STORE_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    expires_raw = ev.get("expires_at", "")
                    if expires_raw:
                        exp = datetime.fromisoformat(expires_raw)
                        if exp.tzinfo is None:
                            exp = exp.replace(tzinfo=UTC)
                        if exp < now:
                            continue
                    events.append(ev)
                except Exception:
                    continue
    except Exception as exc:
        log.debug("macro_event_layer: load failed — %s", exc)
    return events


def _append_event(event: dict) -> None:
    """Append a single event to the JSONL store."""
    os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
    with _LOCK:
        with open(_STORE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")


def _compact_store() -> None:
    """Rewrite store keeping only non-expired events. Called periodically."""
    events = _load_events()
    if not events:
        return
    tmp = _STORE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        os.replace(tmp, _STORE_PATH)
    except Exception as exc:
        log.debug("macro_event_layer: compact failed — %s", exc)


# Compact every ~50 writes
_write_count = 0
_COMPACT_EVERY = 50


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def maybe_record_macro_event(
    headline: str,
    snippet: str = "",
    source: str = "unknown",
    published_at: str | None = None,
) -> str | None:
    """
    Main entry point called from news.py (fail-soft).

    Returns event_id if a macro event was recorded, None if filtered out.
    Never raises.
    """
    global _write_count
    try:
        if not headline or len(headline.strip()) < 10:
            return None

        # Gate 1: keyword pre-filter (cheap)
        if not _passes_keyword_gate(headline, snippet):
            return None

        # Gate 2: deduplication
        if _is_duplicate(headline):
            return None

        # Gate 3: LLM rate limit
        if not _llm_call_allowed():
            log.debug("macro_event_layer: LLM rate limit hit, skipping: %s", headline[:60])
            return None

        # Gate 4: LLM classification
        result = _classify_with_llm(headline, snippet)
        if result is None:
            return None

        if not result.get("macro_significant"):
            log.debug("macro_event_layer: LLM: not macro-significant — %s", headline[:60])
            return None

        event_type = result.get("event_type", "other_macro")
        ttl_hint = result.get("ttl_hours")
        ttl_hours: float | None = float(ttl_hint) if ttl_hint else None

        # Validate and filter drivers to known set
        raw_drivers = result.get("drivers_implicated") or []
        drivers = [d for d in raw_drivers if d in _KNOWN_DRIVERS]

        event: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "event_id": str(uuid.uuid4()),
            "recorded_at": _now_iso(),
            "published_at": published_at or _now_iso(),
            "expires_at": _expires_at(event_type, ttl_hours),
            "source": source,
            "headline": headline,
            "snippet": (snippet or "")[:300],
            "event_type": event_type,
            "event_summary": result.get("event_summary", ""),
            "direction_of_risk": result.get("direction_of_risk", "neutral"),
            "drivers_implicated": drivers,
            "theme_impacts": result.get("theme_impacts") or [],
            "affected_domains": result.get("affected_domains") or [],
            "price_confirmation_signals": result.get("price_confirmation_signals") or [],
            "confidence": float(result.get("confidence", 0.5)),
            "llm_model": "claude-sonnet-4-6",
        }

        _append_event(event)

        _write_count += 1
        if _write_count % _COMPACT_EVERY == 0:
            _compact_store()

        log.info(
            "macro_event_layer: recorded %s [%s] drivers=%s — %s",
            event["event_id"][:8],
            event_type,
            drivers,
            headline[:80],
        )
        return event["event_id"]

    except Exception as exc:
        log.debug("macro_event_layer: maybe_record_macro_event failed — %s", exc)
        return None


def get_recent_events(within_hours: float = 24.0) -> list[dict]:
    """Return non-expired events recorded within the past within_hours."""
    cutoff = datetime.now(UTC) - timedelta(hours=within_hours)
    events = _load_events()
    result = []
    for ev in events:
        try:
            rec = datetime.fromisoformat(ev["recorded_at"])
            if rec.tzinfo is None:
                rec = rec.replace(tzinfo=UTC)
            if rec >= cutoff:
                result.append(ev)
        except Exception:
            result.append(ev)
    return sorted(result, key=lambda e: e.get("recorded_at", ""), reverse=True)


def get_events_for_driver(driver_id: str) -> list[dict]:
    """Return active events that implicate a specific driver."""
    return [
        ev for ev in _load_events()
        if driver_id in (ev.get("drivers_implicated") or [])
    ]


def get_active_context() -> dict[str, Any]:
    """
    Structured context summary consumed by live_driver_resolver.

    Returns:
      {
        "events": [...],                     all active non-expired events
        "drivers_with_event_backing": {      driver_id → list of events
            "oil_supply_shock": [...],
            ...
        },
        "active_domains": [...],             unique domains across all events
        "risk_direction": "risk_off" | "risk_on" | "mixed" | "neutral",
        "generated_at": "..."
      }
    """
    events = _load_events()
    if not events:
        return {
            "events": [],
            "drivers_with_event_backing": {},
            "active_domains": [],
            "risk_direction": "neutral",
            "generated_at": _now_iso(),
        }

    drivers_map: dict[str, list[dict]] = {}
    for ev in events:
        for drv in (ev.get("drivers_implicated") or []):
            drivers_map.setdefault(drv, []).append(ev)

    domains: list[str] = []
    for ev in events:
        for d in (ev.get("affected_domains") or []):
            if d not in domains:
                domains.append(d)

    # Aggregate risk direction: majority vote weighted by confidence
    risk_counts: dict[str, float] = {"risk_off": 0.0, "risk_on": 0.0, "mixed": 0.0, "neutral": 0.0}
    for ev in events:
        direction = ev.get("direction_of_risk", "neutral")
        conf = float(ev.get("confidence", 0.5))
        risk_counts[direction] = risk_counts.get(direction, 0.0) + conf

    risk_direction = max(risk_counts, key=lambda k: risk_counts[k])

    return {
        "events": events,
        "drivers_with_event_backing": drivers_map,
        "active_domains": domains,
        "risk_direction": risk_direction,
        "generated_at": _now_iso(),
    }
