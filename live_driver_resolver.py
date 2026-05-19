"""
live_driver_resolver.py — Live macro driver state resolver.

Single responsibility: fetch real market data via _regime_download() and
translate it into an active_drivers + blocked_conditions dict for
macro_transmission_matrix.fire().

This is the wire between Layer 2 (Market Map) and Layer 1 (Economic
Intelligence). It replaces the hard-coded driver list that was previously
baked into candidate_resolver.generate_feed().

No LLM. No broker. No IBKR. No .env inspection.
Falls back to safe conservative defaults on any data failure so the
intelligence pipeline never crashes due to a market data outage.

Public surface:
    resolve(output_path) -> dict   writes live_driver_state.json, returns it
    load()               -> dict   load most-recent live_driver_state.json
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("decifer.live_driver_resolver")

_OUTPUT_PATH = os.path.join("data", "intelligence", "live_driver_state.json")
_SCHEMA_VERSION = "1.0"

# ── Conservative fallback — used when live data is unavailable ────────────────
# These are the structurally persistent drivers that are almost always true.
# They produce a reasonable universe even when market data cannot be fetched.
_FALLBACK_ACTIVE_DRIVERS = [
    "ai_capex_growth",
    "ai_compute_demand",
]
_FALLBACK_BLOCKED: list[str] = []


def _fetch_5d_return(symbol: str) -> float | None:
    """Fetch 5-day price return for a symbol. Returns None on failure."""
    try:
        from scanner import _regime_download
        df = _regime_download(symbol, period="10d", interval="1d")
        if df is None or len(df) < 2:
            return None
        close = df["Close"].squeeze().dropna()
        if len(close) < 2:
            return None
        # Use last 5 bars or all available if fewer
        n = min(5, len(close) - 1)
        ret = float(close.iloc[-1]) / float(close.iloc[-(n + 1)]) - 1
        return round(ret, 6)
    except Exception as exc:
        log.debug("_fetch_5d_return %s failed: %s", symbol, exc)
        return None


def _fetch_latest_close(symbol: str) -> float | None:
    """Fetch the latest close price for a symbol."""
    try:
        from scanner import _regime_download
        df = _regime_download(symbol, period="5d", interval="1d")
        if df is None or len(df) < 1:
            return None
        close = df["Close"].squeeze().dropna()
        if len(close) < 1:
            return None
        return float(close.iloc[-1])
    except Exception as exc:
        log.debug("_fetch_latest_close %s failed: %s", symbol, exc)
        return None


def resolve(output_path: str = _OUTPUT_PATH) -> dict:
    """
    Fetch live market data, compute driver states, write live_driver_state.json.

    Driver rules (all deterministic, no LLM):
      ai_capex_growth      — SMH 5d return > -8%  (structural; off only on AI capex collapse)
      ai_compute_demand    — NVDA 5d return > -5%  (off only on severe NVDA decline)
      yields_rising        — IEF 5d return < -0.4% (bond price falling = yields rising)
      oil_supply_shock     — USO 5d return > 4% or < -6% (price shock in either direction)
      geopolitical_risk    — ITA 5d return outperforms SPY by > 2% (defence outperformance)
      credit_stress_rising — HYG underperforms LQD by > 0.4% over 5d (credit spread widening)
      risk_off_rotation    — VIX proxy (UVXY) 5d return > 15% OR SPY 5d return < -2.5%

    blocked_conditions: if credit_stress_rising is active, add it to blocked_conditions
    so the banks transmission rule fires conditionally (it has blocked_if: credit_stress_rising).
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    evidence: dict[str, Any] = {}
    warnings: list[str] = []

    # ── Fetch all required bars ───────────────────────────────────────────────
    smh_ret  = _fetch_5d_return("SMH")
    nvda_ret = _fetch_5d_return("NVDA")
    ief_ret  = _fetch_5d_return("IEF")
    uso_ret  = _fetch_5d_return("USO")
    spy_ret  = _fetch_5d_return("SPY")
    ita_ret  = _fetch_5d_return("ITA")
    uvxy_ret = _fetch_5d_return("UVXY")
    hyg_ret  = _fetch_5d_return("HYG")
    lqd_ret  = _fetch_5d_return("LQD")

    evidence = {
        "smh_5d_ret":  smh_ret,
        "nvda_5d_ret": nvda_ret,
        "ief_5d_ret":  ief_ret,
        "uso_5d_ret":  uso_ret,
        "spy_5d_ret":  spy_ret,
        "ita_5d_ret":  ita_ret,
        "uvxy_5d_ret": uvxy_ret,
        "hyg_5d_ret":  hyg_ret,
        "lqd_5d_ret":  lqd_ret,
    }

    fetch_ok = sum(1 for v in evidence.values() if v is not None)
    if fetch_ok < 4:
        warnings.append(
            f"Only {fetch_ok}/9 data fetches succeeded — using fallback driver list"
        )
        log.warning("live_driver_resolver: insufficient data (%d/9) — using fallback", fetch_ok)
        result = _build_result(
            now_iso, _FALLBACK_ACTIVE_DRIVERS, _FALLBACK_BLOCKED,
            evidence, warnings, fallback=True
        )
        _write(result, output_path)
        return result

    # ── Evaluate each driver ─────────────────────────────────────────────────
    active_drivers: list[str] = []
    blocked_conditions: list[str] = []

    # ai_capex_growth: SMH structural tailwind (off only if SMH down >8% in 5d)
    if smh_ret is None or smh_ret > -0.08:
        active_drivers.append("ai_capex_growth")
        evidence["ai_capex_growth_reason"] = f"SMH 5d={_pct(smh_ret)} > -8% threshold"
    else:
        evidence["ai_capex_growth_reason"] = f"SMH 5d={_pct(smh_ret)} collapsed — driver inactive"
        warnings.append("ai_capex_growth inactive: SMH collapsed")

    # ai_compute_demand: NVDA structural (off only if NVDA down >5%)
    if nvda_ret is None or nvda_ret > -0.05:
        active_drivers.append("ai_compute_demand")
        evidence["ai_compute_demand_reason"] = f"NVDA 5d={_pct(nvda_ret)} > -5% threshold"
    else:
        evidence["ai_compute_demand_reason"] = f"NVDA 5d={_pct(nvda_ret)} collapsed — driver inactive"

    # yields_rising: IEF price falling = yields rising
    if ief_ret is not None and ief_ret < -0.004:
        active_drivers.append("yields_rising")
        evidence["yields_rising_reason"] = f"IEF 5d={_pct(ief_ret)} < -0.4% (yields rising)"
    elif ief_ret is not None:
        evidence["yields_rising_reason"] = f"IEF 5d={_pct(ief_ret)} — yields not rising"

    # oil_supply_shock: significant oil move in either direction
    if uso_ret is not None and (uso_ret > 0.04 or uso_ret < -0.06):
        active_drivers.append("oil_supply_shock")
        evidence["oil_supply_shock_reason"] = f"USO 5d={_pct(uso_ret)} — shock threshold crossed"
    elif uso_ret is not None:
        evidence["oil_supply_shock_reason"] = f"USO 5d={_pct(uso_ret)} — no shock"

    # geopolitical_risk_rising: defence outperforming SPY by >2%
    if ita_ret is not None and spy_ret is not None:
        ita_vs_spy = ita_ret - spy_ret
        if ita_vs_spy > 0.02:
            active_drivers.append("geopolitical_risk_rising")
            evidence["geopolitical_reason"] = f"ITA vs SPY={_pct(ita_vs_spy)} > 2% — defence leading"
        else:
            evidence["geopolitical_reason"] = f"ITA vs SPY={_pct(ita_vs_spy)} — no defence outperformance"

    # credit_stress_rising: HYG underperforms LQD by >0.4%
    credit_stressed = False
    if hyg_ret is not None and lqd_ret is not None:
        credit_spread = lqd_ret - hyg_ret  # positive = stress
        credit_stressed = credit_spread > 0.004
        if credit_stressed:
            active_drivers.append("credit_stress_rising")
            blocked_conditions.append("credit_stress_rising")  # blocks banks rule
            evidence["credit_stress_reason"] = f"HYG-LQD spread={_pct(credit_spread)} > 0.4%"
        else:
            evidence["credit_stress_reason"] = f"HYG-LQD spread={_pct(credit_spread)} — credit contained"

    # risk_off_rotation: UVXY up >15% OR SPY down >2.5%
    risk_off = False
    if uvxy_ret is not None and uvxy_ret > 0.15:
        risk_off = True
        evidence["risk_off_reason"] = f"UVXY 5d={_pct(uvxy_ret)} > 15% — vol spike"
    elif spy_ret is not None and spy_ret < -0.025:
        risk_off = True
        evidence["risk_off_reason"] = f"SPY 5d={_pct(spy_ret)} < -2.5% — market decline"
    else:
        evidence["risk_off_reason"] = (
            f"UVXY={_pct(uvxy_ret)} SPY={_pct(spy_ret)} — no risk-off signal"
        )
    if risk_off:
        active_drivers.append("risk_off_rotation")

    log.info(
        "live_driver_resolver: active_drivers=%s blocked=%s (fetch_ok=%d/9)",
        active_drivers, blocked_conditions, fetch_ok
    )

    result = _build_result(
        now_iso, active_drivers, blocked_conditions, evidence, warnings, fallback=False
    )
    _write(result, output_path)
    return result


def _pct(v: float | None) -> str:
    return f"{v * 100:.2f}%" if v is not None else "n/a"


def _build_result(
    generated_at: str,
    active_drivers: list[str],
    blocked_conditions: list[str],
    evidence: dict,
    warnings: list[str],
    fallback: bool,
) -> dict:
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": generated_at,
        "mode": "fallback_conservative" if fallback else "live_market_data",
        "active_drivers": active_drivers,
        "blocked_conditions": blocked_conditions,
        "evidence": evidence,
        "warnings": warnings,
        "live_output_changed": False,
        "broker_called": False,
        "llm_used": False,
    }


def _write(result: dict, output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    os.replace(tmp, output_path)


def load(path: str = _OUTPUT_PATH) -> dict | None:
    """Load the most recent live_driver_state.json. Returns None if missing."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("live_driver_resolver.load failed: %s", exc)
        return None


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(message)s")
    state = resolve()
    print(f"active_drivers:    {state['active_drivers']}")
    print(f"blocked_conditions:{state['blocked_conditions']}")
    print(f"mode:              {state['mode']}")
    for k, v in state.get("evidence", {}).items():
        if isinstance(v, str):
            print(f"  {k}: {v}")
