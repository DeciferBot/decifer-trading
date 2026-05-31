# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  options_flow_scanner.py                   ║
# ║   Single responsibility: scan TTG universe for unusual       ║
# ║   options flow and write leaderboard.json                    ║
# ║   Layer: INTELLIGENCE — no execution imports                 ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝
"""
options_flow_scanner.py — Options flow scan engine for the TTG universe.

Fetches real Alpaca options volume for all active TTG symbols in parallel,
scores each for unusual activity, and writes a ranked leaderboard to
data/options_flow/leaderboard.json.

Runnable standalone (cron-friendly):
  python3 options_flow_scanner.py

Or called by options_flow_api.py for on-demand and universe scans.

No execution imports. No broker state. No trading logic.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("decifer.options_flow_scanner")

_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_TTG_EXPOSURES = _BASE_DIR / "data" / "intelligence" / "theme_graph" / "symbol_exposures.json"
_DRIVER_STATE = _BASE_DIR / "data" / "intelligence" / "live_driver_state.json"
_OUT_DIR = _BASE_DIR / "data" / "options_flow"
_LEADERBOARD_PATH = _OUT_DIR / "leaderboard.json"

_MAX_WORKERS = 12
_MIN_DTE = 7
_MAX_DTE = 45
_FRIDAY_CLOSE_PATH = _OUT_DIR / "leaderboard_friday_close.json"
_OI_NOTE = (
    "Open interest unavailable from current provider (Alpaca). "
    "Signal uses day-over-day volume expansion only."
)


# ---------------------------------------------------------------------------
# Symbol universe reader
# ---------------------------------------------------------------------------

def _ttg_symbols() -> list[str]:
    """Return deduplicated active TTG symbols."""
    try:
        raw = json.loads(_TTG_EXPOSURES.read_text())
        return sorted({
            e["symbol"] for e in raw.get("exposures", [])
            if e.get("status") == "active" and e.get("symbol")
        })
    except Exception as exc:
        log.error("options_flow_scanner: could not load TTG symbols — %s", exc)
        return []


def _active_drivers() -> set[str]:
    try:
        raw = json.loads(_DRIVER_STATE.read_text())
        return set(raw.get("active_drivers", []))
    except Exception:
        return set()


def _symbol_themes(symbols: list[str]) -> dict[str, list[str]]:
    """Map symbol → list of theme_ids from TTG exposures."""
    try:
        raw = json.loads(_TTG_EXPOSURES.read_text())
        result: dict[str, list[str]] = {}
        for e in raw.get("exposures", []):
            sym = e.get("symbol", "")
            if sym in symbols:
                result.setdefault(sym, [])
                tid = e.get("theme_id")
                if tid and tid not in result[sym]:
                    result[sym].append(tid)
        return result
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Per-symbol flow fetch + score
# ---------------------------------------------------------------------------

def _score_symbol(symbol: str) -> dict | None:
    """
    Fetch options flow for one symbol and return a scored dict, or None on failure.
    Designed for ThreadPoolExecutor — no shared mutable state.
    """
    try:
        from options_provider import (
            get_options_flow_data,
            MIN_SIDE_VOLUME,
            MIN_DAY_OVER_DAY_RATIO,
            PREV_VOLUME_FLOOR,
        )
    except ImportError:
        return None

    try:
        flow = get_options_flow_data(symbol, _MIN_DTE, _MAX_DTE)
    except Exception as exc:
        log.debug("options_flow_scanner: %s fetch error — %s", symbol, exc)
        return None

    if flow is None or not flow.flow_metrics_available:
        return None

    def _exp(today: float, prev: float) -> float | None:
        denom = max(prev, PREV_VOLUME_FLOOR)
        return round(today / denom, 2) if today > 0 else None

    call_exp = _exp(flow.call_volume, flow.call_prev_volume)
    put_exp = _exp(flow.put_volume, flow.put_prev_volume)
    unusual_calls = bool(
        flow.call_volume >= MIN_SIDE_VOLUME
        and call_exp is not None
        and call_exp >= MIN_DAY_OVER_DAY_RATIO
    )
    unusual_puts = bool(
        flow.put_volume >= MIN_SIDE_VOLUME
        and put_exp is not None
        and put_exp >= MIN_DAY_OVER_DAY_RATIO
    )

    # Anomaly score (0–10): volume expansion + call/put ratio
    score = 0
    flags: list[str] = []
    if unusual_calls:
        score += 4
        flags.append(f"call volume {call_exp:.1f}× prev day")
    if unusual_puts:
        score += 4
        flags.append(f"put volume {put_exp:.1f}× prev day")
    total_vol = flow.call_volume + flow.put_volume
    if total_vol > 0:
        call_ratio = flow.call_volume / max(flow.put_volume, 1)
        if call_ratio >= 3.0:
            score += 2
            flags.append(f"call/put ratio {call_ratio:.1f}×")
        elif call_ratio >= 2.0:
            score += 1
            flags.append(f"call/put ratio {call_ratio:.1f}×")
        put_ratio = flow.put_volume / max(flow.call_volume, 1)
        if put_ratio >= 3.0:
            score += 2
            flags.append(f"put/call ratio {put_ratio:.1f}×")

    score = min(score, 10)

    return {
        "underlying": symbol,
        "anomaly_score": score,
        "top_score": score * 10,        # normalised 0–100 for leaderboard API compat
        "flags": flags,
        "call_volume": int(flow.call_volume),
        "put_volume": int(flow.put_volume),
        "call_sweep_count": 0,          # REST scan has no sweep data (stream only)
        "put_sweep_count": 0,
        "cluster_count": 0,
        "cross_expiry_count": 0,
        "total_contracts": int(flow.call_volume + flow.put_volume),
        "call_trade_count": int(flow.call_trade_count),
        "call_expansion": call_exp,
        "put_expansion": put_exp,
        "unusual_calls": unusual_calls,
        "unusual_puts": unusual_puts,
        "unusual": unusual_calls or unusual_puts,
        "dominant_side": "CALL" if flow.call_volume > flow.put_volume else "PUT",
        "driver_tags": [],              # populated by scan_universe annotate pass
        "last_event_ts": flow.provider_timestamp,
        "oi_available": False,
        "provider": flow.provider,
        "data_ts": flow.provider_timestamp,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_symbols(symbols: list[str]) -> list[dict]:
    """
    Scan a list of symbols for unusual options flow in parallel.
    Returns list of scored dicts, sorted by anomaly_score desc.
    Only returns symbols with flow_metrics_available (quiet symbols omitted).
    """
    if not symbols:
        return []

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(symbols))) as pool:
        futures = {pool.submit(_score_symbol, sym): sym for sym in symbols}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)

    return sorted(results, key=lambda r: -r["anomaly_score"])


def scan_universe(write: bool = True) -> dict:
    """
    Scan all active TTG symbols and return (optionally writing) the leaderboard.
    Returns the leaderboard payload dict.
    """
    symbols = _ttg_symbols()
    if not symbols:
        return {"status": "error", "message": "TTG symbols unavailable"}

    active_drivers = _active_drivers()
    theme_map = _symbol_themes(symbols)

    log.info("options_flow_scanner: scanning %d TTG symbols …", len(symbols))
    rows = scan_symbols(symbols)

    # Annotate with driver_active, theme_ids, and driver_tags
    from options_flow_engine import DRIVER_TAGS
    for row in rows:
        sym = row["underlying"]
        sym_themes = theme_map.get(sym, [])
        row["theme_ids"] = sym_themes
        row["driver_tags"] = DRIVER_TAGS.get(sym, [])
        # driver_active = any theme driver is currently active
        row["driver_active"] = any(
            d in active_drivers
            for d in [
                e.get("driver_id", "")
                for e in json.loads(_TTG_EXPOSURES.read_text()).get("exposures", [])
                if e.get("symbol") == sym
            ]
        ) if active_drivers else False

    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "scanned": len(symbols),
        "returned": len(rows),
        "unusual_count": sum(1 for r in rows if r["unusual"]),
        "oi_available": False,
        "oi_note": _OI_NOTE,
        "leaderboard": rows,
    }

    if write:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        _LEADERBOARD_PATH.write_text(json.dumps(payload, indent=2))
        log.info(
            "options_flow_scanner: wrote leaderboard — %d symbols, %d unusual",
            len(rows), payload["unusual_count"],
        )

    return payload


def save_friday_close_snapshot() -> dict:
    """Run scan_universe() and persist result as leaderboard_friday_close.json.

    Called once at ~15:55 ET on Fridays by options_flow_monitor. The API falls
    back to this file over the weekend so the leaderboard shows Friday's data
    rather than an empty state.

    Returns the payload dict (same shape as scan_universe()).
    """
    log.info("options_flow_scanner: taking Friday close snapshot")
    payload = scan_universe(write=False)
    payload["friday_close"] = True
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    _FRIDAY_CLOSE_PATH.write_text(json.dumps(payload, indent=2))
    log.info(
        "options_flow_scanner: Friday close snapshot written — %d symbols, %d unusual",
        payload.get("scanned", 0),
        payload.get("unusual_count", 0),
    )
    return payload


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    result = scan_universe(write=True)
    unusual = [r for r in result.get("leaderboard", []) if r["unusual"]]
    print(f"\nScanned {result['scanned']} symbols — {result['unusual_count']} unusual")
    for r in unusual[:20]:
        print(f"  {r['symbol']:6s}  score={r['anomaly_score']}/10  {'; '.join(r['flags'])}")
    sys.exit(0)
