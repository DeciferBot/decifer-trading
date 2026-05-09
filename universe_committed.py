# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  universe_committed.py                     ║
# ║                                                              ║
# ║   Tier B of the three-tier universe model.                  ║
# ║                                                              ║
# ║   Weekly job: enumerate all tradable US equities from       ║
# ║   Alpaca, rank by dollar volume, keep top N (~1000), write  ║
# ║   to data/committed_universe.json.                          ║
# ║                                                              ║
# ║   This list is the "menu" from which the promoter           ║
# ║   (universe_promoter.py) picks its daily top 50.            ║
# ║                                                              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from alpaca_data import fetch_snapshots_batched, get_all_tradable_equities
from config import CONFIG
import worker_evidence as _evidence

log = logging.getLogger("decifer.universe_committed")

_COMMITTED_PATH = os.path.join("data", "committed_universe.json")


# Warrant / right / unit suffixes common on Alpaca. Cheap name-based prefilter;
# the dollar-volume cut downstream catches anything these heuristics miss.
_SKIP_SUFFIXES = (".WS", ".U", ".R", ".WT")


def _is_common_stock(symbol: str) -> bool:
    """Heuristic prefilter: drop warrants, rights, units, preferreds."""
    if not symbol or not symbol.isascii():
        return False
    if any(symbol.endswith(suf) for suf in _SKIP_SUFFIXES):
        return False
    # Symbols with digits in them are often preferreds or when-issued tranches.
    # Keep 4-char-or-less symbols without digits (the overwhelming majority of commons).
    if any(c.isdigit() for c in symbol):
        return False
    # Drop extreme-length symbols (>5 chars typically warrants or units on NYSE)
    if len(symbol) > 5:
        return False
    return True


def refresh_committed_universe(top_n: int | None = None) -> list[dict]:
    """
    Rebuild data/committed_universe.json.

    Flow:
      1. Enumerate all tradable US equities from Alpaca (~12k).
      2. Prefilter by name heuristics (warrants/rights/units/preferreds).
      3. Batch-snapshot all remaining. Compute dollar_volume = prior_close × prev_volume
         (always populated, works weekends — uses last regular session).
      4. Filter: price ≥ $1, prev_volume ≥ 50k, dollar_volume ≥ $1M.
      5. Sort by dollar_volume descending. Keep top N (default from config).
      6. Write data/committed_universe.json.

    Returns the list written. Safe to run at any time (including weekends).
    """
    if top_n is None:
        top_n = int(CONFIG.get("committed_universe_size", 1000))

    # Step 1: enumerate
    all_assets = get_all_tradable_equities()
    if not all_assets:
        log.error("refresh_committed_universe: Alpaca returned zero assets — aborting")
        return []
    log.info(f"refresh_committed_universe: {len(all_assets)} raw tradable equities")

    # Step 2: prefilter by name
    asset_by_sym = {a["symbol"]: a for a in all_assets if _is_common_stock(a["symbol"])}
    log.info(f"refresh_committed_universe: {len(asset_by_sym)} after name prefilter")

    # Step 3: snapshot all in batches (uses prev_volume so works weekends)
    symbols = list(asset_by_sym.keys())
    snaps = fetch_snapshots_batched(symbols, batch_size=100)
    log.info(f"refresh_committed_universe: {len(snaps)} snapshots returned")

    # Step 4: compute dollar volume and filter
    min_price = float(CONFIG.get("committed_min_price", 1.0))
    min_volume = int(CONFIG.get("committed_min_prev_volume", 50_000))
    min_dollar_volume = float(CONFIG.get("committed_min_dollar_volume", 1_000_000))

    ranked: list[dict] = []
    for sym, s in snaps.items():
        try:
            price = s.get("prior_close") or s.get("price")
            vol = s.get("prev_volume") or 0
            if price is None or price < min_price:
                continue
            if vol < min_volume:
                continue
            dv = float(price) * float(vol)
            if dv < min_dollar_volume:
                continue
            ranked.append(
                {
                    "symbol": sym,
                    "dollar_volume": dv,
                    "price": float(price),
                    "prev_volume": int(vol),
                    "exchange": asset_by_sym.get(sym, {}).get("exchange", ""),
                    "fractionable": asset_by_sym.get(sym, {}).get("fractionable", False),
                    "shortable": asset_by_sym.get(sym, {}).get("shortable", False),
                }
            )
        except Exception as exc:
            log.debug(f"refresh_committed_universe: rank skip {sym} — {exc}")

    # Step 5: sort + keep top N
    ranked.sort(key=lambda r: r["dollar_volume"], reverse=True)
    top = ranked[:top_n]
    log.info(
        f"refresh_committed_universe: {len(ranked)} passed filters → keeping top {len(top)} "
        f"(threshold dv=${top[-1]['dollar_volume'] / 1e6:.1f}M)"
        if top
        else f"refresh_committed_universe: {len(ranked)} passed filters → keeping 0"
    )

    # Step 6: write
    payload = {
        "refreshed_at": datetime.now(UTC).isoformat(),
        "count": len(top),
        "threshold_dollar_volume": top[-1]["dollar_volume"] if top else 0,
        "symbols": top,
    }
    os.makedirs(os.path.dirname(_COMMITTED_PATH), exist_ok=True)
    with open(_COMMITTED_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    log.info(f"refresh_committed_universe: wrote {_COMMITTED_PATH}")

    return top


def load_committed_universe() -> list[str]:
    """Load the committed universe as a plain list of symbols. Returns [] on any error."""
    try:
        with open(_COMMITTED_PATH) as f:
            payload = json.load(f)
        return [r["symbol"] for r in payload.get("symbols", [])]
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning(f"load_committed_universe: {exc}")
        return []


# ---------------------------------------------------------------------------
# Standalone worker entry point
# Classification: worker runtime
#
# Run-once mode (weekend-safe, no broker writes, no order placement):
#   python3 -m universe_committed
#   python3 universe_committed.py --run-once
#
# Exits 0 on success, 1 on failure. Writes a heartbeat evidence file to
# data/heartbeats/universe_committed_worker.json on every attempt.
# ---------------------------------------------------------------------------

_HEARTBEAT_PATH = os.path.join("data", "heartbeats", "universe_committed_worker.json")


def _write_heartbeat(
    status: str,
    *,
    count: int = 0,
    elapsed_seconds: float = 0.0,
    artifact_age_seconds: float | None = None,
    error: str | None = None,
) -> None:
    """Write a structured evidence file after each worker attempt."""
    import time as _time

    now_utc = datetime.now(UTC).isoformat()
    artifact_age: float | None = artifact_age_seconds
    if artifact_age is None and os.path.exists(_COMMITTED_PATH):
        try:
            mtime = os.path.getmtime(_COMMITTED_PATH)
            artifact_age = round(_time.time() - mtime, 1)
        except OSError:
            artifact_age = None

    record = {
        "worker": "universe_committed_worker",
        "last_attempt_at": now_utc,
        "last_success_at": now_utc if status == "success" else None,
        "status": status,
        "artifact_path": _COMMITTED_PATH,
        "artifact_age_seconds": artifact_age,
        "count": count,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "error": error,
        "live_output_changed": False,
        "broker_called": False,
        "order_placed": False,
    }
    try:
        os.makedirs(os.path.dirname(_HEARTBEAT_PATH), exist_ok=True)
        tmp = _HEARTBEAT_PATH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(record, fh, indent=2)
        os.replace(tmp, _HEARTBEAT_PATH)
    except OSError as exc:
        log.warning(f"_write_heartbeat: could not write {_HEARTBEAT_PATH} — {exc}")


def _main(argv: list[str] | None = None) -> int:
    """
    Worker entry point. Returns exit code (0=success, 1=failure).

    Always runs once and exits — there is no loop mode. The caller (launchd,
    cron, or a human operator) is responsible for scheduling repetition.
    """
    import argparse
    import time

    parser = argparse.ArgumentParser(
        prog="universe_committed_worker",
        description=(
            "Standalone worker: rebuild data/committed_universe.json. "
            "Weekend-safe. No broker writes. No order placement."
        ),
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        default=True,
        help="Run one refresh cycle and exit (default; flag is explicit-opt-in compatible).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Override committed_universe_size from config.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    t0 = time.monotonic()
    started_at = datetime.now(UTC)
    print(f"[universe_committed_worker] starting run — {started_at.isoformat()}")

    try:
        result = refresh_committed_universe(top_n=args.top_n)
    except Exception as exc:
        finished_at = datetime.now(UTC)
        elapsed = time.monotonic() - t0
        _write_heartbeat("fail", elapsed_seconds=elapsed, error=str(exc))
        _evidence.append_evidence(
            "universe_committed_worker", started_at, finished_at,
            success=False, output_artifact_path=_COMMITTED_PATH,
            failure_reason=str(exc),
        )
        print(f"[universe_committed_worker] FAILED — {exc}", flush=True)
        return 1

    finished_at = datetime.now(UTC)
    elapsed = time.monotonic() - t0

    if not result:
        reason = "refresh returned empty list"
        _write_heartbeat("fail", count=0, elapsed_seconds=elapsed, error=reason)
        _evidence.append_evidence(
            "universe_committed_worker", started_at, finished_at,
            success=False, output_artifact_path=_COMMITTED_PATH,
            failure_reason=reason,
        )
        print("[universe_committed_worker] FAILED — refresh returned empty list", flush=True)
        return 1

    _write_heartbeat("success", count=len(result), elapsed_seconds=elapsed)
    _evidence.append_evidence(
        "universe_committed_worker", started_at, finished_at,
        success=True, output_artifact_path=_COMMITTED_PATH,
        extra={"symbol_count": len(result)},
    )

    print(
        f"[universe_committed_worker] SUCCESS — {len(result)} symbols "
        f"in {elapsed:.1f}s → {_COMMITTED_PATH}",
        flush=True,
    )
    print(f"  top 5 : {[r['symbol'] for r in result[:5]]}")
    print(f"  bottom 5 : {[r['symbol'] for r in result[-5:]]}")
    print(f"  heartbeat : {_HEARTBEAT_PATH}")
    print(f"  evidence  : {_evidence._EVIDENCE_PATH}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
