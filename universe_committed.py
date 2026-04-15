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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    result = refresh_committed_universe()
    print(f"\nTop 10: {[r['symbol'] for r in result[:10]]}")
    print(f"Bottom 5: {[r['symbol'] for r in result[-5:]]}")
