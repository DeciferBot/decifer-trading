"""
thesis_divergence.py — Per-candidate thesis divergence scanner.

Single responsibility: given the economic candidate feed and live driver state,
compute thesis_intact for each economic intelligence candidate and return a
{symbol: bool | None} map for universe_builder to stamp on handoff candidates.

thesis_intact = True   — candidate 5D return is within threshold of its driver proxy
thesis_intact = False  — proxy is positive but candidate lags proxy by > THRESHOLD pp
thesis_intact = None   — no rules fired, headwind/proxy role, or data unavailable

This is a soft diagnostic — it NEVER filters candidates from the universe.
Apex sees the flag and decides what to do with a diverging name.

No LLM. No broker. Fail-soft: if Alpaca is unavailable, all symbols return None
and the pipeline continues normally.

Reads:
    data/intelligence/economic_candidate_feed.json
    data/intelligence/live_driver_state.json
Writes:
    data/intelligence/thesis_divergence.json  (debug artifact)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("decifer.thesis_divergence")

_FEED_PATH    = os.path.join("data", "intelligence", "economic_candidate_feed.json")
_DRIVER_PATH  = os.path.join("data", "intelligence", "live_driver_state.json")
_OUTPUT_PATH  = os.path.join("data", "intelligence", "thesis_divergence.json")

# Map rule_id prefix (before "_to_") → sensor symbol already in driver state evidence.
# Evidence block key: f"{sensor.lower()}_5d_ret"
_RULE_PREFIX_SENSOR: dict[str, str] = {
    "ai_capex_growth":           "SMH",
    "ai_capex":                  "SMH",   # ai_capex_to_memory_storage
    "ai_compute_demand":         "NVDA",
    "geopolitical_risk":         "ITA",
    "yields_falling":            "IEF",
    "yields_rising":             "IEF",
    "risk_on_rotation":          "SPY",
    "risk_off_rotation":         "UVXY",
    "oil_supply_shock":          "USO",
    "oil_shock":                 "USO",
    "credit_stress":             "HYG",
    "credit_stress_easing":      "HYG",
    "gold_bid":                  "GLD",
    "small_cap_risk_on_driver":  "IWM",
}

# Only tailwind beneficiary roles are thesis-checkable.
# ETF proxies and pressure (headwind) candidates stay None.
_TAILWIND_ROLES = {"direct_beneficiary", "second_order_beneficiary"}

# Candidate is diverging when it lags its proxy by more than this many pct-points
# while the proxy itself is positive (driver is actively confirming the theme).
_DIVERGENCE_THRESHOLD_PP = 5.0


def _rule_prefix(rule_id: str) -> str:
    """'ai_capex_growth_to_data_centre_power' → 'ai_capex_growth'."""
    return rule_id.split("_to_")[0] if "_to_" in rule_id else rule_id


def _proxy_sensor_for_rules(rules: list[str]) -> str | None:
    """Return the first matching sensor for the rule list, longest prefix wins."""
    for rule_id in rules:
        prefix = _rule_prefix(rule_id)
        if prefix in _RULE_PREFIX_SENSOR:
            return _RULE_PREFIX_SENSOR[prefix]
    return None


def _proxy_5d_ret(sensor: str, evidence: dict) -> float | None:
    """Read already-fetched 5D return for a sensor from the driver state evidence block."""
    val = evidence.get(f"{sensor.lower()}_5d_ret")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _compute_5d_return(close_series) -> float | None:
    """Compute 5-day return from a pandas Close series."""
    import pandas as pd
    s = pd.Series(close_series).dropna()
    if len(s) < 2:
        return None
    n = min(5, len(s) - 1)
    return round(float(s.iloc[-1]) / float(s.iloc[-(n + 1)]) - 1, 6)


def _fetch_candidate_returns(symbols: list[str]) -> dict[str, float | None]:
    """
    Batch-fetch 5D returns for candidate symbols via Alpaca.
    Returns {symbol: float | None}. Missing symbols map to None (fail-soft).
    """
    if not symbols:
        return {}
    try:
        from alpaca_data import fetch_bars_batch
        bars_map = fetch_bars_batch(symbols, period="10d", interval="1d")
        result: dict[str, float | None] = {}
        for sym in symbols:
            df = bars_map.get(sym)
            if df is None or df.empty:
                result[sym] = None
                continue
            close = df["Close"]
            if hasattr(close, "squeeze"):
                close = close.squeeze()
            result[sym] = _compute_5d_return(close)
        return result
    except Exception as exc:
        log.warning("thesis_divergence: fetch_candidate_returns failed — %s", exc)
        return {sym: None for sym in symbols}


def compute_thesis_divergence(
    feed_path: str = _FEED_PATH,
    driver_path: str = _DRIVER_PATH,
    output_path: str = _OUTPUT_PATH,
) -> dict[str, bool | None]:
    """
    Compute thesis_intact for all economic intelligence candidates.

    Returns {symbol: True | False | None}.
    Writes a debug artifact to output_path.
    Never raises — returns empty dict on total failure.
    """
    try:
        with open(feed_path, encoding="utf-8") as f:
            candidate_feed = json.load(f)
    except Exception as exc:
        log.warning("thesis_divergence: cannot read feed — %s", exc)
        return {}

    try:
        with open(driver_path, encoding="utf-8") as f:
            driver_state = json.load(f)
    except Exception as exc:
        log.warning("thesis_divergence: cannot read driver state — %s", exc)
        return {}

    evidence = driver_state.get("evidence", {})
    candidates = candidate_feed.get("candidates", [])

    # Eligible: tailwind beneficiaries with at least one transmission rule fired
    eligible = [
        c for c in candidates
        if c.get("role") in _TAILWIND_ROLES and c.get("transmission_rules_fired")
    ]

    symbols_to_fetch = list({c["symbol"] for c in eligible if c.get("symbol")})
    candidate_returns = _fetch_candidate_returns(symbols_to_fetch)

    result: dict[str, bool | None] = {}
    detail: list[dict] = []

    for c in candidates:
        sym = c.get("symbol", "")
        if not sym:
            continue

        role = c.get("role", "")
        rules: list[str] = c.get("transmission_rules_fired") or []

        if role not in _TAILWIND_ROLES or not rules:
            result[sym] = None
            continue

        proxy = _proxy_sensor_for_rules(rules)
        if proxy is None:
            result[sym] = None
            continue

        proxy_ret  = _proxy_5d_ret(proxy, evidence)
        cand_ret   = candidate_returns.get(sym)

        if proxy_ret is None or cand_ret is None:
            result[sym] = None
            detail.append({"symbol": sym, "proxy_sensor": proxy,
                           "proxy_5d_pct": None, "candidate_5d_pct": None,
                           "lag_pp": None, "thesis_intact": None,
                           "reason": "data_unavailable", "rules": rules})
            continue

        proxy_pct = proxy_ret * 100
        cand_pct  = cand_ret  * 100
        lag       = proxy_pct - cand_pct

        # Divergence: driver proxy up AND candidate lagging by more than threshold
        intact = not (proxy_pct > 0 and lag > _DIVERGENCE_THRESHOLD_PP)
        reason = (
            f"DIVERGING — proxy {proxy}={proxy_pct:+.2f}% candidate={cand_pct:+.2f}% "
            f"lag={lag:.2f}pp > {_DIVERGENCE_THRESHOLD_PP}pp"
            if not intact else
            f"OK — proxy {proxy}={proxy_pct:+.2f}% candidate={cand_pct:+.2f}% "
            f"lag={lag:.2f}pp ≤ {_DIVERGENCE_THRESHOLD_PP}pp"
        )

        result[sym] = intact
        detail.append({
            "symbol": sym, "proxy_sensor": proxy,
            "proxy_5d_pct":    round(proxy_pct, 2),
            "candidate_5d_pct": round(cand_pct, 2),
            "lag_pp":           round(lag, 2),
            "thesis_intact":    intact,
            "reason":           reason,
            "rules":            rules,
        })

    # Persist debug artifact (fail-soft — never abort pipeline)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "generated_at":     now,
            "total_candidates": len(candidates),
            "eligible_checked": len(eligible),
            "intact_count":     sum(1 for v in result.values() if v is True),
            "diverging_count":  sum(1 for v in result.values() if v is False),
            "unknown_count":    sum(1 for v in result.values() if v is None),
            "threshold_pp":     _DIVERGENCE_THRESHOLD_PP,
            "detail": sorted(
                detail,
                key=lambda x: (x.get("thesis_intact") is not False, -(x.get("lag_pp") or 0)),
            ),
        }
        tmp = output_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, output_path)
        log.info(
            "thesis_divergence: checked=%d intact=%d diverging=%d unknown=%d → %s",
            len(eligible), payload["intact_count"], payload["diverging_count"],
            payload["unknown_count"], output_path,
        )
    except Exception as exc:
        log.warning("thesis_divergence: failed to write debug artifact — %s", exc)

    return result
