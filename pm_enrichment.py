"""
pm_enrichment.py — Track B position enrichment for Apex review context.

Single responsibility: given a list of Track B position dicts (as built by
guardrails.flag_positions_for_review), add four enrichment layers so Apex
receives GPT-quality context for each held position.

Layer 1 — Analyst consensus + price target (FMP, 30-min TTL cache):
    analyst_consensus, analyst_pt, analyst_upside_pct,
    analyst_buy_count, analyst_sell_count, analyst_total

Layer 2 — Price structure (Alpaca, already-cached 5D returns):
    week52_high, week52_high_distance_pct, stock_above_200d, thesis_intact

Layer 3 — Fundamentals (FMP, 24h TTL via warm_fundamentals_cache):
    pe_ratio, is_profitable, revenue_growth_yoy, revenue_decelerating, fcf_yield

Layer 4 — Portfolio theme concentration (zero API calls):
    theme_peers: list[str] (other open positions sharing same primary driver)
    theme_concentration_pct: float (notional of same-driver positions / portfolio_value)

Fail-soft: a failed fetch for any layer leaves those fields None.
The position is never dropped from the review list.

Never called from execution paths. Only from guardrails.flag_positions_for_review().
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

log = logging.getLogger("decifer.pm_enrichment")

_DEFAULT_UNIVERSE_PATH = os.path.join("data", "live", "active_opportunity_universe.json")

# How many threads to use for parallel per-symbol FMP calls
_ENRICH_WORKERS = 6


# ---------------------------------------------------------------------------
# Layer 1 — Analyst consensus + price target
# ---------------------------------------------------------------------------

def _enrich_analyst(symbol: str, current_price: float | None) -> dict[str, Any]:
    """Return analyst enrichment fields for one symbol. Fail-soft."""
    result: dict[str, Any] = {
        "analyst_consensus":   None,
        "analyst_pt":          None,
        "analyst_upside_pct":  None,
        "analyst_buy_count":   None,
        "analyst_sell_count":  None,
        "analyst_total":       None,
    }
    try:
        from fmp_client import get_analyst_consensus, get_price_target, get_analyst_grades
        consensus_data = get_analyst_consensus(symbol)
        if consensus_data:
            result["analyst_consensus"] = consensus_data.get("consensus")

        pt_data = get_price_target(symbol)
        if pt_data:
            pt = pt_data.get("pt_consensus") or pt_data.get("latest_pt")
            result["analyst_pt"] = pt
            if pt and current_price and current_price > 0:
                result["analyst_upside_pct"] = round((pt - current_price) / current_price * 100, 1)

        grades = get_analyst_grades(symbol)
        if grades:
            result["analyst_buy_count"]  = (grades.get("strong_buy") or 0) + (grades.get("buy") or 0)
            result["analyst_sell_count"] = (grades.get("strong_sell") or 0) + (grades.get("sell") or 0)
            result["analyst_total"]      = grades.get("total_analysts")
    except Exception as exc:
        log.debug("pm_enrichment analyst %s: %s", symbol, exc)
    return result


# ---------------------------------------------------------------------------
# Layer 2 — Price structure (52wk high, 200d MA, thesis_intact from handoff)
# ---------------------------------------------------------------------------

def _load_universe_thesis_map(universe_path: str) -> dict[str, bool | None]:
    """Read thesis_intact values from the live universe handoff. Fail-soft."""
    try:
        with open(universe_path, encoding="utf-8") as f:
            data = json.load(f)
        candidates = data.get("candidates") or []
        return {c["symbol"]: c.get("thesis_intact") for c in candidates if c.get("symbol")}
    except Exception as exc:
        log.debug("pm_enrichment: cannot read universe for thesis_intact — %s", exc)
        return {}


def _enrich_price_structure(symbol: str, thesis_intact: bool | None) -> dict[str, Any]:
    """Return price structure enrichment. Fail-soft."""
    result: dict[str, Any] = {
        "week52_high":              None,
        "week52_high_distance_pct": None,
        "stock_above_200d":         None,
        "thesis_intact":            thesis_intact,
    }
    try:
        from alpaca_data import fetch_bars_batch
        bars_map = fetch_bars_batch([symbol], period="252d", interval="1d")
        df = bars_map.get(symbol)
        if df is not None and not df.empty and "Close" in df.columns:
            close = df["Close"].squeeze()
            close_list = close.dropna().tolist()
            if close_list:
                current_px = float(close_list[-1])
                week52_high = float(max(close_list))
                result["week52_high"] = round(week52_high, 2)
                if week52_high > 0:
                    result["week52_high_distance_pct"] = round(
                        (current_px - week52_high) / week52_high * 100, 1
                    )
                result["stock_above_200d"] = len(close_list) >= 200 and current_px > float(
                    sum(close_list[-200:]) / 200
                )
    except Exception as exc:
        log.debug("pm_enrichment price_structure %s: %s", symbol, exc)
    return result


# ---------------------------------------------------------------------------
# Layer 3 — Fundamentals
# ---------------------------------------------------------------------------

def _enrich_fundamentals(symbol: str) -> dict[str, Any]:
    """Return fundamental enrichment fields. Fail-soft."""
    result: dict[str, Any] = {
        "pe_ratio":             None,
        "is_profitable":        None,
        "revenue_growth_yoy":   None,
        "revenue_decelerating": None,
        "fcf_yield":            None,
    }
    try:
        from fmp_client import get_key_metrics_ttm, get_revenue_growth
        metrics = get_key_metrics_ttm(symbol)
        if metrics:
            pe = metrics.get("pe_ratio")
            result["pe_ratio"]      = pe
            nm = metrics.get("net_margin")
            result["is_profitable"] = (nm is not None and nm > 0)
            result["fcf_yield"]     = metrics.get("fcf_yield")

        rev = get_revenue_growth(symbol)
        if rev:
            result["revenue_growth_yoy"]   = rev.get("revenue_growth_yoy")
            result["revenue_decelerating"] = rev.get("revenue_deceleration")
    except Exception as exc:
        log.debug("pm_enrichment fundamentals %s: %s", symbol, exc)
    return result


# ---------------------------------------------------------------------------
# Layer 4 — Portfolio theme concentration (zero API calls)
# ---------------------------------------------------------------------------

def _primary_driver_for_position(pos: dict, universe_candidates: list[dict]) -> str | None:
    """
    Extract the primary macro driver for a held position.

    Priority:
      1. universe handoff macro_rules_fired (most authoritative — same rules that
         placed this symbol in the universe)
      2. entry_thesis field (free-text — extract 'driver:X' pattern if present)
    """
    sym = (pos.get("symbol") or "").upper()
    for c in universe_candidates:
        if (c.get("symbol") or "").upper() == sym:
            rules = c.get("macro_rules_fired") or []
            if rules:
                # e.g. "ai_capex_growth_to_data_centre_power" → "ai_capex_growth"
                first = rules[0]
                return first.split("_to_")[0] if "_to_" in first else first
    return None


def _enrich_theme_concentration(
    pos: dict,
    all_positions: list[dict],
    universe_candidates: list[dict],
    portfolio_value: float,
) -> dict[str, Any]:
    """
    Compute theme peer group and concentration for one position.
    Returns theme_peers: list[str], theme_concentration_pct: float | None.
    """
    result: dict[str, Any] = {"theme_peers": [], "theme_concentration_pct": None}
    try:
        sym = (pos.get("symbol") or "").upper()
        my_driver = _primary_driver_for_position(pos, universe_candidates)
        if not my_driver:
            return result

        peers: list[str] = []
        peer_notional = 0.0
        for other in all_positions:
            other_sym = (other.get("symbol") or "").upper()
            if other_sym == sym:
                continue
            other_driver = _primary_driver_for_position(other, universe_candidates)
            if other_driver == my_driver:
                peers.append(other_sym)
                qty     = abs(float(other.get("qty") or 0))
                px      = float(other.get("current") or other.get("entry") or 0)
                peer_notional += qty * px

        result["theme_peers"] = peers
        if portfolio_value and portfolio_value > 0:
            # Include this position's own notional in the concentration
            own_qty = abs(float(pos.get("qty") or 0))
            own_px  = float(pos.get("current") or pos.get("entry") or 0)
            total_notional = peer_notional + own_qty * own_px
            result["theme_concentration_pct"] = round(total_notional / portfolio_value * 100, 1)
    except Exception as exc:
        log.debug("pm_enrichment theme_concentration %s: %s", pos.get("symbol"), exc)
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def enrich_review_positions(
    positions: list[dict],
    all_open_positions: "list[dict] | dict | None" = None,
    portfolio_value: float = 0.0,
    universe_path: str = _DEFAULT_UNIVERSE_PATH,
) -> list[dict]:
    """
    Enrich a list of Track B position dicts with 4 layers of context.

    Modifies positions in-place (also returns them for convenience).
    Fail-soft: any exception leaves fields None, never removes a position.

    Args:
        positions:           Track B position dicts from guardrails.flag_positions_for_review
        all_open_positions:  all open positions (dict or list) for theme concentration
        portfolio_value:     total portfolio NLV for concentration % calculation
        universe_path:       path to active_opportunity_universe.json for thesis_intact + drivers
    """
    if not positions:
        return positions

    # Normalise all_open_positions to list
    if isinstance(all_open_positions, dict):
        all_positions_list: list[dict] = list(all_open_positions.values())
    elif isinstance(all_open_positions, list):
        all_positions_list = all_open_positions
    else:
        all_positions_list = []

    # Pre-load universe data once (thesis_intact + macro_rules_fired for concentration)
    thesis_map = _load_universe_thesis_map(universe_path)
    universe_candidates: list[dict] = []
    try:
        with open(universe_path, encoding="utf-8") as f:
            universe_candidates = json.load(f).get("candidates") or []
    except Exception:
        pass

    symbols = [p.get("symbol") or "" for p in positions]

    # Pre-warm fundamentals cache for all symbols in one shot (avoids N×2 FMP calls)
    try:
        from fmp_client import warm_fundamentals_cache
        valid_syms = [s for s in symbols if s]
        if valid_syms:
            warm_fundamentals_cache(valid_syms)
    except Exception as exc:
        log.debug("pm_enrichment: warm_fundamentals_cache skipped — %s", exc)

    # Per-symbol enrichment layers 1-3 in parallel
    enrichment_by_sym: dict[str, dict] = {}

    def _enrich_one(pos: dict) -> tuple[str, dict]:
        sym = pos.get("symbol") or ""
        current_price = pos.get("current_price") or pos.get("current")
        try:
            current_price = float(current_price) if current_price is not None else None
        except (TypeError, ValueError):
            current_price = None

        thesis_intact = thesis_map.get(sym)
        combined: dict[str, Any] = {}
        combined.update(_enrich_analyst(sym, current_price))
        combined.update(_enrich_price_structure(sym, thesis_intact))
        combined.update(_enrich_fundamentals(sym))
        return sym, combined

    with ThreadPoolExecutor(max_workers=_ENRICH_WORKERS) as pool:
        futures = {pool.submit(_enrich_one, p): p for p in positions}
        for fut in as_completed(futures):
            try:
                sym, data = fut.result()
                enrichment_by_sym[sym] = data
            except Exception as exc:
                log.debug("pm_enrichment worker error: %s", exc)

    # Apply enrichment + Layer 4 per position
    for pos in positions:
        sym = pos.get("symbol") or ""
        enr = enrichment_by_sym.get(sym, {})
        pos.update(enr)

        # Layer 4 — theme concentration (uses all_positions_list, no I/O)
        theme_data = _enrich_theme_concentration(
            pos, all_positions_list, universe_candidates, portfolio_value
        )
        pos.update(theme_data)

    log.info(
        "pm_enrichment: enriched %d Track B positions (analyst/price/fundamentals/theme)",
        len(positions),
    )
    return positions
