"""
live_driver_resolver.py — Live macro driver state resolver.

Single responsibility: fetch real market data via _regime_download() and
translate it into an active_drivers + blocked_conditions dict for
macro_transmission_matrix.fire().

This is the wire between Layer 2 (Market Map) and Layer 1 (Economic
Intelligence). It replaces the hard-coded driver list that was previously
baked into candidate_resolver.generate_feed().

No LLM. No broker. No IBKR. No .env inspection.
Fails closed on data failure — no default-to-AI fallback. If all sensors fail,
returns empty active_drivers with mode="no_data_available". If some sensors
succeed, evaluates what it can with mode="degraded_partial_data".

Public surface:
    resolve(output_path) -> dict   writes live_driver_state.json, returns it
    load()               -> dict   load most-recent live_driver_state.json

Drivers resolved (deterministic, no LLM):
  ai_capex_growth       — SMH 5d return > -8%  (structural; off only on AI capex collapse)
  ai_compute_demand     — NVDA 5d return > -5%  (off only on severe NVDA decline)
  yields_rising         — IEF 5d return < -0.4% (bond price falling = yields rising)
  yields_falling        — IEF 5d return > +0.4% (bond price rising = yields falling)
  oil_supply_shock      — USO 5d return > 4% or < -6% (price shock in either direction)
  geopolitical_risk_rising  — ITA outperforms SPY by > 2% (defence outperformance)
  geopolitical_risk_falling — ITA underperforms SPY by > 1.5% OR (USO < -5% AND ITA
                              not leading by > 1%) — peace pricing / de-escalation
  credit_stress_rising  — HYG underperforms LQD by > 0.4% over 5d
  risk_off_rotation     — UVXY 5d return > 15% OR SPY 5d return < -2.5%
  risk_on_rotation      — UVXY 5d return < -10% AND SPY 5d return > +1.5%
  gold_safe_haven_bid   — GLD 5d return > +2%
  credit_stress_easing  — HYG outperforms LQD by > 0.4% over 5d
  small_cap_risk_on     — IWM outperforms SPY by > 1.5% over 5d

  futures_risk_on       — ES 5d return > +0.75% (advisory evidence only)
  futures_risk_off      — ES 5d return < -0.75% (advisory evidence only)

blocked_conditions:
  credit_stress_rising  — added when that driver fires (blocks banks rule)
  smh_tactical_weakness — added when SMH 5d return between -4% and -8%

Futures sensors (ES=F, NQ=F) are fetched via futures_data.py (yfinance) after
the 11-sensor core block. They do not affect the fail-closed count or degraded
mode, and are not wired into transmission_rules.json — evidence/narrative only.

Data fetch strategy (approved yfinance exception):
  _fetch_5d_return and _fetch_latest_close use Alpaca as the primary source.
  When Alpaca returns None (cloud server, weekend market closure, missing creds),
  yfinance is used as a fallback for the 9+2 ETF sensors. This mirrors the
  futures_data.py pattern and is listed in tests/test_no_yfinance_runtime.py
  _YFINANCE_APPROVED. The fail-closed guarantee is preserved: if both Alpaca
  and yfinance fail, the sensor returns None and the fail-closed path fires.
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

# ── Fail-closed defaults — no data, no drivers ───────────────────────────────
# Never default to AI drivers. If live data is unavailable, return empty.
# This prevents stale AI-only universe generation when market data fails.
_FALLBACK_ACTIVE_DRIVERS: list[str] = []
_FALLBACK_BLOCKED: list[str] = []


def _compute_5d_return(close_series) -> float | None:
    """Compute 5-day return from a Close series."""
    import pandas as pd
    s = pd.Series(close_series).dropna()
    if len(s) < 2:
        return None
    n = min(5, len(s) - 1)
    return round(float(s.iloc[-1]) / float(s.iloc[-(n + 1)]) - 1, 6)


def _fetch_5d_return(symbol: str) -> float | None:
    """Fetch 5-day price return. Primary: Alpaca. Fallback: yfinance."""
    # Primary: Alpaca via scanner._regime_download
    try:
        from scanner import _regime_download
        df = _regime_download(symbol, period="10d", interval="1d")
        if df is not None and len(df) >= 2:
            ret = _compute_5d_return(df["Close"].squeeze())
            if ret is not None:
                return ret
    except Exception as exc:
        log.debug("_fetch_5d_return %s Alpaca failed: %s", symbol, exc)

    # Fallback: yfinance (cloud server / weekend market closure)
    try:
        import yfinance as yf
        df = yf.download(symbol, period="10d", interval="1d", progress=False, auto_adjust=True)
        if df is not None and len(df) >= 2:
            ret = _compute_5d_return(df["Close"].squeeze())
            if ret is not None:
                log.debug("_fetch_5d_return %s: yfinance fallback used", symbol)
                return ret
    except Exception as exc:
        log.debug("_fetch_5d_return %s yfinance fallback failed: %s", symbol, exc)

    return None


def _fetch_latest_close(symbol: str) -> float | None:
    """Fetch the latest close price. Primary: Alpaca. Fallback: yfinance."""
    # Primary: Alpaca
    try:
        from scanner import _regime_download
        df = _regime_download(symbol, period="5d", interval="1d")
        if df is not None and len(df) >= 1:
            close = df["Close"].squeeze().dropna()
            if len(close) >= 1:
                return float(close.iloc[-1])
    except Exception as exc:
        log.debug("_fetch_latest_close %s Alpaca failed: %s", symbol, exc)

    # Fallback: yfinance
    try:
        import yfinance as yf
        df = yf.download(symbol, period="5d", interval="1d", progress=False, auto_adjust=True)
        if df is not None and len(df) >= 1:
            close = df["Close"].squeeze().dropna()
            if len(close) >= 1:
                log.debug("_fetch_latest_close %s: yfinance fallback used", symbol)
                return float(close.iloc[-1])
    except Exception as exc:
        log.debug("_fetch_latest_close %s yfinance fallback failed: %s", symbol, exc)

    return None


def resolve(output_path: str = _OUTPUT_PATH) -> dict:
    """
    Fetch live market data, compute driver states, write live_driver_state.json.

    Fails closed: if all sensors fail, returns empty active_drivers and
    mode="no_data_available". Never defaults to AI drivers.
    If 1-10 sensors succeed, evaluates available drivers with
    mode="degraded_partial_data" warning.

    See module docstring for full driver list and thresholds.
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    evidence: dict[str, Any] = {}
    warnings: list[str] = []

    # ── Fetch all required bars (11 sensors) ─────────────────────────────────
    smh_ret  = _fetch_5d_return("SMH")
    nvda_ret = _fetch_5d_return("NVDA")
    ief_ret  = _fetch_5d_return("IEF")
    uso_ret  = _fetch_5d_return("USO")
    spy_ret  = _fetch_5d_return("SPY")
    ita_ret  = _fetch_5d_return("ITA")
    uvxy_ret = _fetch_5d_return("UVXY")
    hyg_ret  = _fetch_5d_return("HYG")
    lqd_ret  = _fetch_5d_return("LQD")
    gld_ret  = _fetch_5d_return("GLD")
    iwm_ret  = _fetch_5d_return("IWM")

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
        "gld_5d_ret":  gld_ret,
        "iwm_5d_ret":  iwm_ret,
    }

    fetch_ok = sum(1 for v in evidence.values() if v is not None)

    # ── Fail closed if no data available ─────────────────────────────────────
    if fetch_ok == 0:
        warnings.append("All 11 data fetches failed — no drivers resolved (fail-closed)")
        log.warning("live_driver_resolver: all sensors failed — returning no_data_available")
        result = _build_result(
            now_iso, [], [],
            evidence, warnings, mode="no_data_available"
        )
        _write(result, output_path)
        return result

    # ── Degraded warning if partial data ─────────────────────────────────────
    _total_sensors = 11
    if fetch_ok < _total_sensors:
        warnings.append(
            f"Only {fetch_ok}/{_total_sensors} data fetches succeeded — "
            f"evaluating available drivers (degraded_partial_data)"
        )
        log.warning(
            "live_driver_resolver: partial data (%d/%d) — evaluating available sensors",
            fetch_ok, _total_sensors
        )

    # ── Evaluate each driver — only when its sensor(s) are available ─────────
    active_drivers: list[str] = []
    blocked_conditions: list[str] = []

    # ai_capex_growth: SMH structural tailwind (off only if SMH down >8% in 5d)
    if smh_ret is not None and smh_ret > -0.08:
        active_drivers.append("ai_capex_growth")
        evidence["ai_capex_growth_reason"] = f"SMH 5d={_pct(smh_ret)} > -8% threshold"
    elif smh_ret is not None:
        evidence["ai_capex_growth_reason"] = f"SMH 5d={_pct(smh_ret)} collapsed — driver inactive"
        warnings.append("ai_capex_growth inactive: SMH collapsed")
    else:
        evidence["ai_capex_growth_reason"] = "SMH unavailable — driver skipped"

    # ai_compute_demand: NVDA structural (off only if NVDA down >5%)
    if nvda_ret is not None and nvda_ret > -0.05:
        active_drivers.append("ai_compute_demand")
        bp_from_threshold = int((-0.05 - nvda_ret) * 10000)  # negative = bp above threshold
        proximity_note = (
            f" ⚠ {abs(bp_from_threshold)}bp from deactivation"
            if bp_from_threshold > -100 else ""  # warn if within 100bp of -5%
        )
        evidence["ai_compute_demand_reason"] = f"NVDA 5d={_pct(nvda_ret)} > -5% threshold{proximity_note}"
    elif nvda_ret is not None:
        evidence["ai_compute_demand_reason"] = f"NVDA 5d={_pct(nvda_ret)} collapsed — driver inactive"
    else:
        evidence["ai_compute_demand_reason"] = "NVDA unavailable — driver skipped"

    # smh_tactical_weakness: blocked condition (not a driver) when SMH between -4% and -8%
    if smh_ret is not None and smh_ret < -0.04:
        blocked_conditions.append("smh_tactical_weakness")
        evidence["smh_tactical_weakness_reason"] = (
            f"SMH 5d={_pct(smh_ret)} between -4% and -8% — tactical weakness blocker"
        )

    # yields_rising: IEF price falling = yields rising
    if ief_ret is not None and ief_ret < -0.004:
        active_drivers.append("yields_rising")
        evidence["yields_rising_reason"] = f"IEF 5d={_pct(ief_ret)} < -0.4% (yields rising)"
    elif ief_ret is not None:
        evidence["yields_rising_reason"] = f"IEF 5d={_pct(ief_ret)} — yields not rising"

    # yields_falling: IEF price rising = yields falling
    if ief_ret is not None and ief_ret > 0.004:
        active_drivers.append("yields_falling")
        evidence["yields_falling_reason"] = f"IEF 5d={_pct(ief_ret)} > +0.4% (yields falling)"
    else:
        evidence["yields_falling_reason"] = f"IEF 5d={_pct(ief_ret)} — yields not falling"

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

    # geopolitical_risk_falling: peace pricing / de-escalation signal.
    # Primary: ITA underperforms SPY by >1.5% (defence fading as catalyst eases).
    # Secondary: oil collapsing (USO < -5%) while ITA is not strongly leading
    # (vs_spy < +1%) — captures early peace-pricing where oil leads defence.
    # The two paths cover both late-stage (defence has rolled) and early-stage
    # (oil reacts first) de-escalation regimes.
    if ita_ret is not None and spy_ret is not None:
        ita_vs_spy_falling = spy_ret - ita_ret  # positive = defence underperforming
        oil_collapsing = uso_ret is not None and uso_ret < -0.05
        defence_not_leading = (ita_ret - spy_ret) < 0.01
        primary = ita_vs_spy_falling > 0.015
        secondary = oil_collapsing and defence_not_leading
        if primary or secondary:
            active_drivers.append("geopolitical_risk_falling")
            if primary:
                evidence["geopolitical_falling_reason"] = (
                    f"ITA underperforms SPY by {_pct(ita_vs_spy_falling)} — defence fading"
                )
            else:
                evidence["geopolitical_falling_reason"] = (
                    f"USO 5d={_pct(uso_ret)} collapsing + defence not leading "
                    f"(ITA-SPY={_pct(ita_ret - spy_ret)}) — peace pricing"
                )
        else:
            evidence["geopolitical_falling_reason"] = (
                f"ITA-SPY={_pct(ita_ret - spy_ret)}, USO={_pct(uso_ret)} — "
                f"no de-escalation signal"
            )

    # credit_stress_rising: HYG underperforms LQD by >0.4%
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

    # risk_on_rotation: UVXY falling and SPY rising
    if uvxy_ret is not None and spy_ret is not None and uvxy_ret < -0.10 and spy_ret > 0.015:
        active_drivers.append("risk_on_rotation")
        evidence["risk_on_reason"] = f"UVXY={_pct(uvxy_ret)} < -10% AND SPY={_pct(spy_ret)} > +1.5%"
    else:
        evidence["risk_on_reason"] = f"UVXY={_pct(uvxy_ret)} SPY={_pct(spy_ret)} — no risk-on signal"

    # gold_safe_haven_bid: GLD up meaningfully
    if gld_ret is not None and gld_ret > 0.02:
        active_drivers.append("gold_safe_haven_bid")
        evidence["gold_reason"] = f"GLD 5d={_pct(gld_ret)} > +2% — safe-haven bid"
    elif gld_ret is not None:
        evidence["gold_reason"] = f"GLD 5d={_pct(gld_ret)} — no safe-haven signal"
    else:
        evidence["gold_reason"] = "GLD unavailable"

    # credit_stress_easing: HYG outperforms LQD (opposite of credit_stress_rising)
    if hyg_ret is not None and lqd_ret is not None:
        credit_improvement = hyg_ret - lqd_ret  # positive = HYG outperforming = easing
        if credit_improvement > 0.004:
            active_drivers.append("credit_stress_easing")
            evidence["credit_easing_reason"] = f"HYG outperforms LQD by {_pct(credit_improvement)} — credit easing"
        else:
            evidence["credit_easing_reason"] = f"HYG-LQD={_pct(credit_improvement)} — credit not easing"

    # small_cap_risk_on: IWM outperforms SPY meaningfully
    if iwm_ret is not None and spy_ret is not None:
        iwm_vs_spy = iwm_ret - spy_ret
        if iwm_vs_spy > 0.015:
            active_drivers.append("small_cap_risk_on")
            evidence["small_cap_risk_on_reason"] = f"IWM vs SPY={_pct(iwm_vs_spy)} > +1.5%"
        else:
            evidence["small_cap_risk_on_reason"] = f"IWM vs SPY={_pct(iwm_vs_spy)} — no small-cap leadership"

    # ── Futures sensors (advisory — do not affect core sensor count) ─────────
    # ES=F and NQ=F via futures_data.py (yfinance). Fetched after fetch_ok so
    # futures failure never triggers degraded mode for the core 11 sensors.
    try:
        from futures_data import fetch_futures_returns
        es_ret, nq_ret = fetch_futures_returns()
    except Exception:
        es_ret, nq_ret = None, None

    evidence["es_5d_ret"] = es_ret
    evidence["nq_5d_ret"] = nq_ret

    # futures_risk_on: broad market and tech futures both advancing
    if es_ret is not None and es_ret > 0.0075:
        active_drivers.append("futures_risk_on")
        nq_note = f", NQ={_pct(nq_ret)}" if nq_ret is not None else ""
        bp_margin = int((es_ret - 0.0075) * 10000)
        margin_note = f" — {bp_margin}bp above threshold, marginal" if bp_margin < 50 else ""
        evidence["futures_risk_on_reason"] = (
            f"ES 5d={_pct(es_ret)} > +0.75%{nq_note} — futures bullish{margin_note}"
        )
    elif es_ret is not None:
        nq_note = f", NQ={_pct(nq_ret)}" if nq_ret is not None else ""
        bp_from = int((0.0075 - es_ret) * 10000)
        approaching = f" — {bp_from}bp below threshold" if 0 < bp_from < 50 else ""
        evidence["futures_risk_on_reason"] = (
            f"ES 5d={_pct(es_ret)}{nq_note} — futures not bullish{approaching}"
        )
    else:
        evidence["futures_risk_on_reason"] = "ES=F unavailable — futures sensor skipped"

    # futures_risk_off: broad market futures declining
    if es_ret is not None and es_ret < -0.0075:
        active_drivers.append("futures_risk_off")
        nq_note = f", NQ={_pct(nq_ret)}" if nq_ret is not None else ""
        evidence["futures_risk_off_reason"] = (
            f"ES 5d={_pct(es_ret)} < -0.75%{nq_note} — futures bearish"
        )
    elif es_ret is not None:
        evidence["futures_risk_off_reason"] = (
            f"ES 5d={_pct(es_ret)} — futures not bearish"
        )

    mode = "live_market_data" if fetch_ok == _total_sensors else "degraded_partial_data"

    log.info(
        "live_driver_resolver: active_drivers=%s blocked=%s (fetch_ok=%d/%d)",
        active_drivers, blocked_conditions, fetch_ok, _total_sensors
    )

    # ── Macro Event Layer annotation ──────────────────────────────────────────
    # Read active macro events and annotate each driver with confirmation status.
    # Macro events are the primary signal; price sensors are the confirmation gate.
    # If no macro event exists for a driver, price-only activation is unchanged.
    macro_context: dict[str, Any] = {}
    driver_confirmation: dict[str, str] = {}
    event_unconfirmed_drivers: list[str] = []
    try:
        from macro_event_layer import get_active_context
        macro_context = get_active_context()
        event_backed = macro_context.get("drivers_with_event_backing", {})

        for driver, evs in event_backed.items():
            if not evs:
                continue
            if driver in active_drivers:
                driver_confirmation[driver] = "CONFIRMED"
            else:
                driver_confirmation[driver] = "EVENT_UNCONFIRMED"
                event_unconfirmed_drivers.append(driver)

        for driver in list(active_drivers):
            if driver not in driver_confirmation:
                driver_confirmation[driver] = "PRICE_ONLY"

    except Exception as _mac_exc:
        log.debug("live_driver_resolver: macro_event_layer read failed — %s", _mac_exc)

    result = _build_result(
        now_iso, active_drivers, blocked_conditions, evidence, warnings, mode=mode,
        driver_confirmation=driver_confirmation,
        event_unconfirmed_drivers=event_unconfirmed_drivers,
        macro_context_summary={
            "active_domains": macro_context.get("active_domains", []),
            "risk_direction": macro_context.get("risk_direction", "neutral"),
            "event_count": len(macro_context.get("events", [])),
        },
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
    mode: str = "live_market_data",
    driver_confirmation: dict | None = None,
    event_unconfirmed_drivers: list[str] | None = None,
    macro_context_summary: dict | None = None,
) -> dict:
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": generated_at,
        "mode": mode,
        "active_drivers": active_drivers,
        "blocked_conditions": blocked_conditions,
        "evidence": evidence,
        "warnings": warnings,
        "driver_confirmation": driver_confirmation or {},
        "event_unconfirmed_drivers": event_unconfirmed_drivers or [],
        "macro_context_summary": macro_context_summary or {},
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
    try:
        import conviction_cache as _conv
        changed_drivers = set(result.get("active_drivers", []))
        import json as _json, os as _os
        _exp_path = _os.path.join(_os.path.dirname(output_path), "theme_graph", "symbol_exposures.json")
        if _os.path.exists(_exp_path):
            _exp = _json.loads(open(_exp_path).read())
            affected = [e.get("symbol", "").upper() for e in _exp.get("exposures", [])
                        if e.get("driver_id") in changed_drivers and e.get("status") == "active"]
            if affected:
                _conv.trigger_rescore(affected, reason="driver_state_change")
    except Exception as _exc:
        pass  # conviction wiring is non-critical


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
