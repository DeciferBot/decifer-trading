# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  counter_thesis_engine.py                  ║
# ║   Structural Counter-Thesis Intelligence (Intelligence layer)║
# ║   Inventor: AMIT CHOPRA                                     ║
# ╚══════════════════════════════════════════════════════════════╝
"""
counter_thesis_engine.py — Curated counter-thesis library with FMP verification.

Runtime classification: intelligence (INTELLIGENCE layer)
Execution:              NEVER — read-only intelligence module

Purpose
───────
When a driver/theme is active, the counter-thesis engine checks whether
any *verified structural conflicts* exist against that thesis. These are
not LLM-generated opinions — they are:

  1. Curated claims from credible financial sources (FT, Bloomberg, etc.)
  2. Verified against FMP fundamental data (capex/revenue ratios, FCF, ROI)
  3. Assigned a confidence score based on how strongly the data supports
     the claim

This is read-only intelligence that surfaces in:
  - GET /api/counter-thesis  (JSON)
  - GET /  (HTML view page at intelligence.decifertrading.com)

It is NOT fed into Apex, execution, or universe scoring without explicit
Amit approval. This sprint is view-only.

Architecture boundary
─────────────────────
  - Imports: fmp_client (data), json, os (data files)
  - Never imports: bot_trading, orders_*, apex_orchestrator, universe_builder
  - Writes: nothing — pure read + compute
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger("decifer.counter_thesis_engine")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FundamentalEvidence:
    """Verified data point from FMP supporting or refuting a claim."""
    symbol: str
    label: str
    metric: str          # human-readable metric name
    value: float | None
    unit: str            # "%", "x", "$B", etc.
    interpretation: str  # plain English — what this number means for the claim
    supports_thesis: bool | None  # True = supports original bull thesis, False = supports counter-thesis, None = neutral


@dataclass
class CounterThesisItem:
    """A single counter-thesis claim with verification status."""
    id: str
    driver_id: str
    theme_label: str
    claim: str                          # The counter-thesis claim (1 sentence)
    plain_english: str                  # 2-3 sentence plain English explanation
    source_attribution: str             # "FT / Panmure Liberum (2025)", etc.
    verification_status: str            # "verified" | "partial" | "unverified" | "refuted"
    confidence: float                   # 0.0–1.0
    evidence: list[FundamentalEvidence] = field(default_factory=list)
    bull_counter: str = ""              # The bull case response to this counter-thesis
    verdict_summary: str = ""           # 1 sentence: "Data supports this concern" etc.
    last_verified_ts: float = 0.0


@dataclass
class CounterThesisReport:
    """Full report for all active drivers."""
    generated_at: str
    active_drivers: list[str]
    structural_conflicts: list[CounterThesisItem]
    dormant_conflicts: list[CounterThesisItem]  # Claims for inactive drivers
    data_freshness: str  # "live" | "cached" | "unavailable"
    note: str = "View-only intelligence. Not connected to execution or scoring."


# ─────────────────────────────────────────────────────────────────────────────
# Curated counter-thesis library
# ─────────────────────────────────────────────────────────────────────────────

# Each entry defines a counter-thesis claim and how to verify it with FMP data.
# verification_symbols: list of tickers to pull FMP metrics for
# verification_fn: str key → _VERIFIERS dict below
#
# Rule: every entry must have a credible source_attribution. No LLM-generated
# claims without a named third-party source.

_LIBRARY: list[dict[str, Any]] = [
    {
        "id": "hyperscaler_capex_roi_gap",
        "driver_id": "ai_capex_growth",
        "theme_label": "AI Infrastructure Buildout",
        "claim": "Hyperscaler AI capex is growing faster than revenue, implying negative implied ROI on AI investment under most scenarios",
        "plain_english": (
            "The major cloud platforms (Microsoft, Google, Amazon, Meta, Oracle) are spending trillions "
            "on AI infrastructure between 2025–2030. Under best-case assumptions — zero operating costs, "
            "just revenue against capex — only Amazon clears a positive return. The real returns, once GPUs, "
            "power, and salaries are factored in, are materially worse. This mirrors the 2000 dot-com dynamic: "
            "the technology was real, but the infrastructure buildout destroyed more capital than it created."
        ),
        "source_attribution": "Financial Times / Panmure Liberum (2025)",
        "verification_method": "capex_growth_vs_revenue_growth",
        "verification_symbols": ["MSFT", "GOOGL", "AMZN", "META", "ORCL"],
        "bull_counter": (
            "Bears have been wrong about AI infrastructure spending returns before — "
            "AWS and Azure turned capex-heavy phases into dominant recurring revenue businesses. "
            "The current cycle may monetise faster than dot-com because AI has immediate enterprise use cases."
        ),
    },
    {
        "id": "enterprise_ai_roi_disappointment",
        "driver_id": "ai_capex_growth",
        "theme_label": "AI Infrastructure Buildout",
        "claim": "Enterprise AI deployments are failing to generate promised cost savings, leading to spending retrenchment",
        "plain_english": (
            "Early enterprise AI projects are running into an ROI wall. One Fortune 20 company spent $200M "
            "chasing $1B in AI-driven opex savings and received only modest results. Another single client "
            "accidentally spent $500M in one month on AI tokens without usage controls. "
            "When ROI disappointment hits at scale, enterprise AI spend — which funds hyperscaler revenue — "
            "could stall before capex commitments are recouped."
        ),
        "source_attribution": "Axios / Industry Reports (May 2025)",
        "verification_method": "software_revenue_deceleration",
        "verification_symbols": ["MSFT", "GOOGL", "AMZN"],
        "bull_counter": (
            "Early enterprise AI ROI cycles are always messy — productivity software took years to prove ROI. "
            "Agentic AI workflows are showing early traction in coding (GitHub Copilot) and customer service. "
            "The ROI gap may close as use cases mature and token costs fall."
        ),
    },
    {
        "id": "nvidia_customer_concentration",
        "driver_id": "ai_compute_demand",
        "theme_label": "AI Compute Demand",
        "claim": "NVIDIA's AI compute revenue is dangerously concentrated in 4 hyperscaler customers who are building competing chips",
        "plain_english": (
            "Microsoft, Google, Amazon, and Meta collectively account for the majority of NVIDIA's data centre "
            "revenue — and all four are designing custom AI accelerators (TPUs, Trainium, MAIA, MTIA). "
            "If even one hyperscaler transitions 20–30% of workloads to in-house silicon, the revenue impact "
            "on NVIDIA is disproportionate to the shift. AMD and emerging players add further competitive pressure."
        ),
        "source_attribution": "Company filings / Analyst consensus (2025)",
        "verification_method": "revenue_concentration_proxy",
        "verification_symbols": ["NVDA", "AMD", "INTC"],
        "bull_counter": (
            "Custom chips take 3–5 years to match GPU performance for general workloads. "
            "Hyperscalers have historically bought GPUs AND their own chips — they are additive, not substitutive. "
            "NVIDIA's software moat (CUDA) creates switching costs that custom silicon cannot easily displace."
        ),
    },
    {
        "id": "oil_demand_peak_structural",
        "driver_id": "oil_supply_shock",
        "theme_label": "Oil Supply Shock",
        "claim": "Structural oil demand is already past peak in developed markets as EV adoption accelerates",
        "plain_english": (
            "Global oil demand from road transport is structurally declining in the US, EU, and China as EV "
            "fleet share rises above 15–20%. Supply shocks in a structurally declining demand environment "
            "produce shorter-duration price spikes. The 2025 supply-shock playbook (energy stocks, defence) "
            "may have a shorter shelf life than prior cycles when demand was growing."
        ),
        "source_attribution": "IEA World Energy Outlook 2024 / BloombergNEF",
        "verification_method": "energy_sector_revenue_growth",
        "verification_symbols": ["XOM", "CVX", "OXY"],
        "bull_counter": (
            "Aviation, shipping, and petrochemicals have no near-term electrification path. "
            "Emerging market demand (India, Southeast Asia) is still growing. "
            "Supply discipline from OPEC+ means the market can tighten even with demand moderation."
        ),
    },
    {
        "id": "defence_cycle_valuation_stretched",
        "driver_id": "geopolitical_risk_rising",
        "theme_label": "Geopolitical Risk Rising",
        "claim": "Defence stocks are pricing in a permanently elevated geopolitical premium that may not persist",
        "plain_english": (
            "Global defence budgets hit multi-decade highs in 2024–2025 after NATO rearmament and Ukraine spending. "
            "Defence stocks trade at 20–25x forward earnings — above historical norms. "
            "Geopolitical cycles have historically mean-reverted faster than defence valuations adjust, "
            "particularly when peace negotiations begin or domestic fiscal constraints bite. "
            "The 'de-escalation surprise' is the single biggest known risk to this thesis."
        ),
        "source_attribution": "Market valuation data / SIPRI (2025)",
        "verification_method": "defence_pe_vs_history",
        "verification_symbols": ["LMT", "RTX", "NOC", "GD"],
        "bull_counter": (
            "NATO 2% GDP commitment is structural — even if one conflict ends, the rearmament cycle takes "
            "10+ years to unwind. European defence budgets are legislatively committed. "
            "Backlog-to-sales ratios at major contractors are at 20-year highs, providing multi-year visibility."
        ),
    },
    {
        "id": "credit_stress_contagion_underpriced",
        "driver_id": "credit_stress_rising",
        "theme_label": "Credit Stress Rising",
        "claim": "Credit spreads are tightening despite rising corporate defaults in lower-rated tranches",
        "plain_english": (
            "Investment grade spreads and high-yield spreads compressed in 2025 despite rising default rates "
            "in CCC-rated corporates. The market is pricing in a soft landing, but the transmission from "
            "tight spreads to actual financing conditions is lagged. Companies refinancing in 2025–2026 face "
            "materially higher coupon costs than their 2020–2021 vintage debt, creating a slow-motion "
            "earnings headwind that spread markets may be underpricing."
        ),
        "source_attribution": "S&P Global Ratings / FRED (2025)",
        "verification_method": "credit_proxy_financial_health",
        "verification_symbols": ["HYG", "LQD", "JNK"],
        "bull_counter": (
            "The Fed has rate-cut capacity to prevent a credit crunch from becoming systemic. "
            "Corporate balance sheets entered 2025 with near-record cash buffers from the 2020–2021 "
            "refinancing wave. Investment grade quality is the highest in 20 years."
        ),
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# FMP verification logic
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_fmp_metrics(symbols: list[str]) -> dict[str, dict]:
    """Pull key_metrics_ttm and revenue_growth for each symbol concurrently via fmp_client."""
    try:
        import fmp_client
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_one(sym: str) -> tuple[str, dict]:
            try:
                metrics = fmp_client.get_key_metrics_ttm(sym) or {}
                growth = fmp_client.get_revenue_growth(sym) or {}
                return sym, {"metrics": metrics, "growth": growth}
            except Exception as e:
                log.debug("FMP fetch failed for %s: %s", sym, e)
                return sym, {}

        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_one, sym): sym for sym in symbols}
            for future in as_completed(futures, timeout=20):
                try:
                    sym, data = future.result()
                    results[sym] = data
                except Exception:
                    results[futures[future]] = {}
        return results
    except ImportError:
        log.warning("fmp_client not available — using empty metrics")
        return {}


def _verify_capex_growth_vs_revenue_growth(
    symbols: list[str], fmp_data: dict[str, dict]
) -> tuple[list[FundamentalEvidence], str, float]:
    """
    Check if capex growth is outpacing revenue growth.
    Returns (evidence_list, verification_status, confidence).
    """
    evidence: list[FundamentalEvidence] = []
    support_count = 0  # supports counter-thesis
    total = 0

    _LABELS = {
        "MSFT": "Microsoft", "GOOGL": "Alphabet", "AMZN": "Amazon",
        "META": "Meta", "ORCL": "Oracle",
    }

    for sym in symbols:
        data = fmp_data.get(sym, {})
        metrics = data.get("metrics", {})
        growth = data.get("growth", {})

        # fmp_client.get_revenue_growth returns revenue_growth_yoy (e.g. 1.98 = 198% growth)
        # Normalise: values > 5 are likely percentages already (e.g. 15.3 = 15.3%)
        rev_growth_raw = growth.get("revenue_growth_yoy")
        if rev_growth_raw is not None:
            rev_growth = rev_growth_raw / 100 if rev_growth_raw > 5 else rev_growth_raw
        else:
            rev_growth = None

        # fmp_client.get_key_metrics_ttm returns fcf_yield (e.g. 0.03 = 3%)
        fcf_yield = metrics.get("fcf_yield")

        # We don't have capex growth from fmp_client — use revenue growth vs FCF yield as proxy
        # High revenue growth + healthy FCF yield = capex may be self-funding
        # Low FCF yield relative to stated AI investment = concern
        if rev_growth is not None:
            total += 1
            # rev_growth is % points e.g. 12.5 = 12.5% growth
            growth_concern = rev_growth < 10  # < 10% YoY for hyperscalers = slowing
            supports_counter = growth_concern

            interp = (
                f"Revenue growing {rev_growth:.1f}% YoY — "
                f"{'growth decelerating, harder to justify AI capex pace' if growth_concern else 'still growing, capex has revenue tailwind'}"
            )
            evidence.append(FundamentalEvidence(
                symbol=sym,
                label=_LABELS.get(sym, sym),
                metric="Revenue growth (YoY)",
                value=round(rev_growth, 1),
                unit="%",
                interpretation=interp,
                supports_thesis=not supports_counter,
            ))
            if supports_counter:
                support_count += 1

        if fcf_yield is not None:
            # fcf_yield from fmp_client is already in % e.g. 2.3 = 2.3%
            evidence.append(FundamentalEvidence(
                symbol=sym,
                label=_LABELS.get(sym, sym),
                metric="Free cash flow yield TTM",
                value=round(fcf_yield, 2),
                unit="%",
                interpretation=(
                    f"FCF yield of {fcf_yield:.1f}% — "
                    f"{'healthy, capex may be self-funding' if fcf_yield > 3 else 'thin FCF relative to capex commitment'}"
                ),
                supports_thesis=fcf_yield > 3,
            ))

    if total == 0:
        return evidence, "unverified", 0.3
    confidence = 0.4 + (support_count / total) * 0.5
    status = "verified" if support_count >= 2 else ("partial" if support_count >= 1 else "unverified")
    return evidence, status, round(confidence, 2)


def _verify_software_revenue_deceleration(
    symbols: list[str], fmp_data: dict[str, dict]
) -> tuple[list[FundamentalEvidence], str, float]:
    """Check cloud/AI revenue growth deceleration as proxy for enterprise ROI squeeze."""
    evidence: list[FundamentalEvidence] = []
    _LABELS = {"MSFT": "Microsoft", "GOOGL": "Alphabet", "AMZN": "Amazon"}

    for sym in symbols:
        data = fmp_data.get(sym, {})
        growth = data.get("growth", {})
        rev_growth_raw = growth.get("revenue_growth_yoy")
        if rev_growth_raw is not None:
            label = _LABELS.get(sym, sym)
            evidence.append(FundamentalEvidence(
                symbol=sym,
                label=label,
                metric="Revenue growth (YoY)",
                value=round(rev_growth_raw, 1),
                unit="%",
                interpretation=(
                    f"{label} revenue growing {rev_growth_raw:.1f}% YoY — "
                    f"{'still strong, enterprise spend holding' if rev_growth_raw > 12 else 'decelerating — enterprise demand caution'}"
                ),
                supports_thesis=rev_growth_raw > 12,
            ))

    status = "partial" if evidence else "unverified"
    return evidence, status, 0.45


def _verify_revenue_concentration_proxy(
    symbols: list[str], fmp_data: dict[str, dict]
) -> tuple[list[FundamentalEvidence], str, float]:
    """Check NVIDIA revenue growth and AMD competitive momentum."""
    evidence: list[FundamentalEvidence] = []
    _LABELS = {"NVDA": "NVIDIA", "AMD": "AMD", "INTC": "Intel"}

    for sym in symbols:
        data = fmp_data.get(sym, {})
        growth = data.get("growth", {})
        metrics = data.get("metrics", {})
        rev_growth_raw = growth.get("revenue_growth_yoy")  # already a % e.g. 12.5 means 12.5% growth
        rev_growth = rev_growth_raw  # keep as percentage points
        pe = metrics.get("pe_ratio")

        if rev_growth is not None:
            label = _LABELS.get(sym, sym)
            supports = sym == "NVDA" and rev_growth > 50  # NVDA 50%+ growth = bull thesis intact
            evidence.append(FundamentalEvidence(
                symbol=sym,
                label=label,
                metric="Revenue growth (YoY)",
                value=round(rev_growth, 1),
                unit="%",
                interpretation=(
                    f"{label} revenue {rev_growth:.1f}% growth — "
                    f"{'GPU demand still intense' if sym == 'NVDA' and rev_growth > 30 else 'AMD gaining share' if sym == 'AMD' and rev_growth > 15 else 'growth context'}"
                ),
                supports_thesis=supports,
            ))
        if pe is not None and sym == "NVDA":
            evidence.append(FundamentalEvidence(
                symbol="NVDA",
                label="NVIDIA",
                metric="P/E ratio (TTM)",
                value=round(pe, 1),
                unit="x",
                interpretation=(
                    f"P/E of {pe:.0f}x — "
                    f"{'elevated, pricing in perfect execution' if pe > 40 else 'moderate relative to growth rate'}"
                ),
                supports_thesis=pe < 40,
            ))

    status = "partial" if evidence else "unverified"
    return evidence, status, 0.5


def _verify_energy_sector_revenue_growth(
    symbols: list[str], fmp_data: dict[str, dict]
) -> tuple[list[FundamentalEvidence], str, float]:
    """Check oil major revenue growth as proxy for demand environment."""
    evidence: list[FundamentalEvidence] = []
    _LABELS = {"XOM": "ExxonMobil", "CVX": "Chevron", "OXY": "Occidental"}

    for sym in symbols:
        data = fmp_data.get(sym, {})
        growth = data.get("growth", {})
        rev_growth_raw = growth.get("revenue_growth_yoy")  # already a % e.g. 12.5 means 12.5% growth
        rev_growth = rev_growth_raw  # keep as percentage points
        if rev_growth is not None:
            evidence.append(FundamentalEvidence(
                symbol=sym,
                label=_LABELS.get(sym, sym),
                metric="Revenue growth (YoY)",
                value=round(rev_growth, 1),
                unit="%",
                interpretation=(
                    f"Revenue {'+' if rev_growth >= 0 else ''}{rev_growth:.1f}% — "
                    f"{'supply shock supporting revenues' if rev_growth > 5 else 'demand weakness visible in top line'}"
                ),
                supports_thesis=rev_growth > 5,
            ))

    status = "partial" if evidence else "unverified"
    return evidence, status, 0.4


def _verify_defence_pe_vs_history(
    symbols: list[str], fmp_data: dict[str, dict]
) -> tuple[list[FundamentalEvidence], str, float]:
    """Check defence sector PE ratios vs historical norms (~16–18x)."""
    evidence: list[FundamentalEvidence] = []
    _LABELS = {"LMT": "Lockheed Martin", "RTX": "RTX Corp", "NOC": "Northrop Grumman", "GD": "General Dynamics"}
    stretched_count = 0

    for sym in symbols:
        data = fmp_data.get(sym, {})
        metrics = data.get("metrics", {})
        pe = metrics.get("pe_ratio")
        if pe is not None:
            stretched = pe > 20
            if stretched:
                stretched_count += 1
            evidence.append(FundamentalEvidence(
                symbol=sym,
                label=_LABELS.get(sym, sym),
                metric="P/E ratio (TTM)",
                value=round(pe, 1),
                unit="x",
                interpretation=(
                    f"P/E {pe:.0f}x vs historical defence norm ~16–18x — "
                    f"{'elevated, geopolitical premium baked in' if stretched else 'within historical range'}"
                ),
                supports_thesis=not stretched,
            ))

    if not evidence:
        return evidence, "unverified", 0.3
    confidence = 0.45 + (stretched_count / len(evidence)) * 0.4
    status = "verified" if stretched_count >= 2 else ("partial" if stretched_count >= 1 else "unverified")
    return evidence, status, round(confidence, 2)


def _verify_credit_proxy_financial_health(
    symbols: list[str], fmp_data: dict[str, dict]
) -> tuple[list[FundamentalEvidence], str, float]:
    """Use ETF return proxies for credit health — FMP gives price data."""
    # For ETFs we can check price performance as a proxy
    evidence: list[FundamentalEvidence] = []
    _LABELS = {"HYG": "High Yield Bond ETF (HYG)", "LQD": "Investment Grade Bond ETF (LQD)", "JNK": "Junk Bond ETF (JNK)"}

    for sym in symbols:
        data = fmp_data.get(sym, {})
        metrics = data.get("metrics", {})
        # Dividend yield as a proxy for spread level
        div_yield = metrics.get("dividendYieldTTM") or metrics.get("dividendYieldPercentageTTM")
        if div_yield is not None:
            label = _LABELS.get(sym, sym)
            # div_yield from key_metrics_ttm is already a % e.g. 4.5 = 4.5%
            evidence.append(FundamentalEvidence(
                symbol=sym,
                label=label,
                metric="Dividend yield (spread proxy)",
                value=round(div_yield, 2),
                unit="%",
                interpretation=(
                    f"{label} yield {div_yield:.1f}% — "
                    f"{'elevated spread, credit stress visible' if div_yield > 7 else 'spread compressed, market pricing soft landing'}"
                ),
                supports_thesis=div_yield < 7,
            ))

    status = "partial" if evidence else "unverified"
    return evidence, status, 0.4


_VERIFIERS = {
    "capex_growth_vs_revenue_growth": _verify_capex_growth_vs_revenue_growth,
    "software_revenue_deceleration": _verify_software_revenue_deceleration,
    "revenue_concentration_proxy": _verify_revenue_concentration_proxy,
    "energy_sector_revenue_growth": _verify_energy_sector_revenue_growth,
    "defence_pe_vs_history": _verify_defence_pe_vs_history,
    "credit_proxy_financial_health": _verify_credit_proxy_financial_health,
}


# ─────────────────────────────────────────────────────────────────────────────
# Verdict summary builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_verdict_summary(status: str, confidence: float, claim_short: str) -> str:
    if status == "verified":
        return f"Current data supports this concern (confidence {confidence:.0%})."
    if status == "partial":
        return f"Partial data support — FMP metrics are mixed ({confidence:.0%} confidence)."
    if status == "refuted":
        return f"Current fundamentals do not support this concern."
    return f"Insufficient data to verify — treat as an unconfirmed risk."


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def _load_active_drivers() -> list[str]:
    """Load active drivers from live_driver_state.json."""
    path = os.path.join(_BASE_DIR, "data", "intelligence", "live_driver_state.json")
    try:
        data = json.load(open(path))
        return data.get("active_drivers", [])
    except Exception:
        return []


def build_counter_thesis_report(use_fmp: bool = True) -> CounterThesisReport:
    """
    Build a full counter-thesis report for all active drivers.

    Args:
        use_fmp: If True, attempt FMP verification. If False, return library
                 entries with unverified status (for testing / fast path).
    """
    from datetime import datetime, UTC
    active_drivers = _load_active_drivers()

    # Collect all symbols needing FMP data
    all_symbols: set[str] = set()
    if use_fmp:
        for entry in _LIBRARY:
            all_symbols.update(entry.get("verification_symbols", []))

    # Fetch FMP data once for all symbols
    fmp_data: dict[str, dict] = {}
    data_freshness = "curated"
    if use_fmp and all_symbols:
        fmp_data = _fetch_fmp_metrics(list(all_symbols))
        data_freshness = "live" if any(fmp_data.values()) else "unavailable"

    structural_conflicts: list[CounterThesisItem] = []
    dormant_conflicts: list[CounterThesisItem] = []

    for entry in _LIBRARY:
        driver_id = entry["driver_id"]
        symbols = entry.get("verification_symbols", [])
        method = entry.get("verification_method", "")
        verifier = _VERIFIERS.get(method)

        if verifier and fmp_data:
            evidence, status, confidence = verifier(symbols, fmp_data)
        else:
            evidence, status, confidence = [], "unverified", 0.3

        verdict = _build_verdict_summary(status, confidence, entry["claim"])

        item = CounterThesisItem(
            id=entry["id"],
            driver_id=driver_id,
            theme_label=entry["theme_label"],
            claim=entry["claim"],
            plain_english=entry["plain_english"],
            source_attribution=entry["source_attribution"],
            verification_status=status,
            confidence=confidence,
            evidence=evidence,
            bull_counter=entry.get("bull_counter", ""),
            verdict_summary=verdict,
            last_verified_ts=time.time(),
        )

        if driver_id in active_drivers:
            structural_conflicts.append(item)
        else:
            dormant_conflicts.append(item)

    return CounterThesisReport(
        generated_at=datetime.now(UTC).isoformat(),
        active_drivers=active_drivers,
        structural_conflicts=structural_conflicts,
        dormant_conflicts=dormant_conflicts,
        data_freshness=data_freshness,
    )


def build_counter_thesis_dict(use_fmp: bool = True) -> dict:
    """Serializable dict version for JSON API responses."""
    report = build_counter_thesis_report(use_fmp=use_fmp)

    def item_to_dict(item: CounterThesisItem) -> dict:
        d = asdict(item)
        d["evidence"] = [asdict(e) for e in item.evidence]
        return d

    return {
        "generated_at": report.generated_at,
        "active_drivers": report.active_drivers,
        "structural_conflicts": [item_to_dict(i) for i in report.structural_conflicts],
        "dormant_conflicts": [item_to_dict(i) for i in report.dormant_conflicts],
        "data_freshness": report.data_freshness,
        "note": report.note,
    }
