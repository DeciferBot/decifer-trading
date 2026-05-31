#!/usr/bin/env python3
"""
scripts/backfill_friday_close.py

Reconstruct a Friday close leaderboard from historical Alpaca option bars.

How it works:
  1. Get the current active option chain per TTG symbol (OCC symbols that
     exist today and haven't expired yet — the same contracts existed on
     the target Friday since our 7–45 DTE window means expiry ≥ June 8).
  2. Fetch daily bars for those OCC symbols for the target Friday and the
     prior Thursday.
  3. Aggregate call/put volumes per day, compute expansion, score exactly
     like options_flow_scanner._score_symbol.
  4. Write data/options_flow/leaderboard_friday_close.json.

Usage:
  python3 scripts/backfill_friday_close.py                    # defaults to last Fri/Thu
  python3 scripts/backfill_friday_close.py 2026-05-30         # specific Friday date
"""
from __future__ import annotations

import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import dotenv

dotenv.load_dotenv()

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("backfill_friday_close")

_OUT_PATH = _REPO / "data" / "options_flow" / "leaderboard_friday_close.json"
_TTG_EXPOSURES = _REPO / "data" / "intelligence" / "theme_graph" / "symbol_exposures.json"
_DRIVER_STATE = _REPO / "data" / "intelligence" / "live_driver_state.json"

MIN_DTE = 7
MAX_DTE = 45
MIN_SIDE_VOLUME = 250
MIN_DAY_OVER_DAY_RATIO = 1.75
PREV_VOLUME_FLOOR = 50
MAX_WORKERS = 8
# Alpaca accepts up to 1000 symbols per bar request
BARS_BATCH_SIZE = 500


def _ttg_symbols() -> list[str]:
    raw = json.loads(_TTG_EXPOSURES.read_text())
    return sorted({
        e["symbol"] for e in raw.get("exposures", [])
        if e.get("status") == "active" and e.get("symbol")
    })


def _active_drivers() -> set[str]:
    try:
        return set(json.loads(_DRIVER_STATE.read_text()).get("active_drivers", []))
    except Exception:
        return set()


def _make_client():
    from alpaca.data.historical.option import OptionHistoricalDataClient
    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    return OptionHistoricalDataClient(api_key, secret_key, raw_data=True)


def _last_friday() -> date:
    """Return the most recent Friday that was a trading day (skips Saturday/Sunday)."""
    today = date.today()
    # weekday(): Mon=0 ... Fri=4 ... Sat=5 ... Sun=6
    days_back = (today.weekday() - 4) % 7
    if days_back == 0:
        days_back = 7
    candidate = today - timedelta(days=days_back)
    # Sanity: must actually be a Friday
    assert candidate.weekday() == 4, f"Expected Friday, got {candidate.strftime('%A')}"
    return candidate


def _get_chain_symbols(client, symbol: str, target_friday: date) -> list[str]:
    """Return OCC symbols in the 7–45 DTE window (relative to target Friday)."""
    from alpaca.data.requests import OptionChainRequest
    date_min = target_friday + timedelta(days=MIN_DTE)
    date_max = target_friday + timedelta(days=MAX_DTE)
    try:
        req = OptionChainRequest(
            underlying_symbol=symbol,
            expiration_date_gte=date_min,
            expiration_date_lte=date_max,
        )
        raw = client.get_option_chain(req)
        return list(raw.keys()) if raw else []
    except Exception as exc:
        log.debug("chain fetch failed for %s: %s", symbol, exc)
        return []


def _fetch_bars_batch(client, occ_symbols: list[str], target_friday: date) -> dict[str, dict]:
    """
    Fetch daily bars for occ_symbols on target_friday and the prior day.
    Returns {occ_symbol: {"friday_vol": float, "thursday_vol": float, "opt_type": str}}
    """
    from alpaca.data.requests import OptionBarsRequest
    from alpaca.data.timeframe import TimeFrame

    thursday = target_friday - timedelta(days=1)
    # Fetch a 2-day window; Alpaca returns bars only for days with activity
    start_dt = datetime(thursday.year, thursday.month, thursday.day, tzinfo=UTC)
    end_dt = datetime(target_friday.year, target_friday.month, target_friday.day, 23, 59, tzinfo=UTC)

    import re
    _OCC_RE = re.compile(r"^([A-Z ]{1,6})(\d{6})([CP])(\d{8})$")

    results: dict[str, dict] = {}
    for i in range(0, len(occ_symbols), BARS_BATCH_SIZE):
        batch = occ_symbols[i:i + BARS_BATCH_SIZE]
        try:
            req = OptionBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start_dt,
                end=end_dt,
            )
            raw = client.get_option_bars(req)
        except Exception as exc:
            log.debug("bars batch failed: %s", exc)
            continue

        if not raw:
            continue

        # raw is {occ_symbol: [list of bar dicts]} when raw_data=True
        for occ_sym, bars in raw.items():
            m = _OCC_RE.match(occ_sym.strip())
            if not m:
                continue
            opt_type = m.group(3)  # 'C' or 'P'

            friday_vol = 0.0
            thursday_vol = 0.0
            for bar in (bars if isinstance(bars, list) else []):
                bar_date_str = bar.get("t", "")[:10]  # "YYYY-MM-DD"
                v = float(bar.get("v", 0) or 0)
                if bar_date_str == str(target_friday):
                    friday_vol += v
                elif bar_date_str == str(thursday):
                    thursday_vol += v

            results[occ_sym] = {
                "friday_vol": friday_vol,
                "thursday_vol": thursday_vol,
                "opt_type": opt_type,
            }
    return results


def _score_from_bars(symbol: str, bar_data: dict[str, dict]) -> dict | None:
    """Aggregate bar data into call/put volumes and score like _score_symbol."""
    call_friday = 0.0
    call_thursday = 0.0
    put_friday = 0.0
    put_thursday = 0.0

    for d in bar_data.values():
        if d["opt_type"] == "C":
            call_friday += d["friday_vol"]
            call_thursday += d["thursday_vol"]
        else:
            put_friday += d["friday_vol"]
            put_thursday += d["thursday_vol"]

    if call_friday == 0 and put_friday == 0:
        return None

    def _exp(today: float, prev: float) -> float | None:
        denom = max(prev, PREV_VOLUME_FLOOR)
        return round(today / denom, 2) if today > 0 else None

    call_exp = _exp(call_friday, call_thursday)
    put_exp = _exp(put_friday, put_thursday)
    unusual_calls = bool(call_friday >= MIN_SIDE_VOLUME and call_exp is not None and call_exp >= MIN_DAY_OVER_DAY_RATIO)
    unusual_puts = bool(put_friday >= MIN_SIDE_VOLUME and put_exp is not None and put_exp >= MIN_DAY_OVER_DAY_RATIO)

    score = 0
    flags: list[str] = []
    if unusual_calls:
        score += 4
        flags.append(f"call volume {call_exp:.1f}× prev day")
    if unusual_puts:
        score += 4
        flags.append(f"put volume {put_exp:.1f}× prev day")

    total_vol = call_friday + put_friday
    if total_vol > 0:
        call_ratio = call_friday / max(put_friday, 1)
        if call_ratio >= 3.0:
            score += 2
            flags.append(f"call/put ratio {call_ratio:.1f}×")
        elif call_ratio >= 2.0:
            score += 1
            flags.append(f"call/put ratio {call_ratio:.1f}×")
        put_ratio = put_friday / max(call_friday, 1)
        if put_ratio >= 3.0:
            score += 2
            flags.append(f"put/call ratio {put_ratio:.1f}×")

    score = min(score, 10)

    return {
        "underlying": symbol,
        "anomaly_score": score,
        "top_score": score * 10,
        "flags": flags,
        "call_volume": int(call_friday),
        "put_volume": int(put_friday),
        "call_sweep_count": 0,
        "put_sweep_count": 0,
        "cluster_count": 0,
        "cross_expiry_count": 0,
        "total_contracts": int(call_friday + put_friday),
        "call_trade_count": 0,
        "call_expansion": call_exp,
        "put_expansion": put_exp,
        "unusual_calls": unusual_calls,
        "unusual_puts": unusual_puts,
        "unusual": unusual_calls or unusual_puts,
        "dominant_side": "CALL" if call_friday > put_friday else "PUT",
        "driver_tags": [],
        "last_event_ts": datetime.now(UTC).isoformat(),
        "oi_available": False,
        "provider": "alpaca_historical_bars_backfill",
        "data_ts": datetime.now(UTC).isoformat(),
    }


def run(target_friday: date) -> None:
    thursday = target_friday - timedelta(days=1)
    log.info("Backfilling Friday close: %s vs %s", target_friday, thursday)

    client = _make_client()
    symbols = _ttg_symbols()
    log.info("TTG universe: %d symbols", len(symbols))

    # Step 1: collect OCC symbols per underlying
    log.info("Fetching option chains...")
    chain_map: dict[str, list[str]] = {}

    def _fetch_chain(sym):
        return sym, _get_chain_symbols(client, sym, target_friday)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for sym, occs in pool.map(_fetch_chain, symbols):
            if occs:
                chain_map[sym] = occs

    total_occ = sum(len(v) for v in chain_map.values())
    log.info("Got %d OCC symbols across %d underlyings", total_occ, len(chain_map))

    # Step 2: fetch historical bars for all OCC symbols
    log.info("Fetching historical bars for %s and %s...", target_friday, thursday)
    all_occ = [occ for occs in chain_map.values() for occ in occs]
    all_bars = _fetch_bars_batch(client, all_occ, target_friday)
    log.info("Got bars for %d OCC symbols", len(all_bars))

    # Step 3: score per underlying
    rows: list[dict] = []
    for sym, occ_list in chain_map.items():
        sym_bars = {occ: all_bars[occ] for occ in occ_list if occ in all_bars}
        if not sym_bars:
            continue
        row = _score_from_bars(sym, sym_bars)
        if row:
            rows.append(row)

    rows.sort(key=lambda r: -r["anomaly_score"])

    # Step 4: annotate with driver/theme info (same as scan_universe)
    active_drivers = _active_drivers()
    try:
        ttg_raw = json.loads(_TTG_EXPOSURES.read_text())
        theme_map: dict[str, list[str]] = {}
        for e in ttg_raw.get("exposures", []):
            sym = e.get("symbol", "")
            tid = e.get("theme_id")
            if sym and tid:
                theme_map.setdefault(sym, [])
                if tid not in theme_map[sym]:
                    theme_map[sym].append(tid)
    except Exception:
        theme_map = {}

    for row in rows:
        sym = row["underlying"]
        row["theme_ids"] = theme_map.get(sym, [])
        row["driver_active"] = False  # not annotating retroactively

    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "friday_date": str(target_friday),
        "thursday_date": str(thursday),
        "friday_close": True,
        "backfill": True,
        "scanned": len(symbols),
        "returned": len(rows),
        "unusual_count": sum(1 for r in rows if r["unusual"]),
        "oi_available": False,
        "oi_note": "Open interest unavailable from Alpaca. Signal uses day-over-day volume expansion only.",
        "leaderboard": rows,
    }

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(payload, indent=2))
    log.info(
        "Written %s — %d symbols returned, %d unusual",
        _OUT_PATH, len(rows), payload["unusual_count"],
    )
    unusual = [r for r in rows if r["unusual"]]
    if unusual:
        print(f"\nTop unusual ({len(unusual)} total):")
        for r in unusual[:15]:
            print(f"  {r['underlying']:6s}  score={r['anomaly_score']}/10  {'; '.join(r['flags'])}")
    else:
        print("No symbols crossed the unusual threshold.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = date.fromisoformat(sys.argv[1])
    else:
        target = _last_friday()
    run(target)
