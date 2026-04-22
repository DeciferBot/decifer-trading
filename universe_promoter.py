# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  universe_promoter.py                      ║
# ║                                                              ║
# ║   Tier B daily promotion.                                   ║
# ║                                                              ║
# ║   Fires at 16:15 ET (post-close) and 08:00 ET (pre-open).   ║
# ║   Reads committed_universe.json (~1000 names), scores each, ║
# ║   writes the top 50 to data/daily_promoted.json for the     ║
# ║   main scanner to consume at the next cycle.                ║
# ║                                                              ║
# ║   v1 scoring: gap + pm_vol_ratio + catalyst. 5d_return and  ║
# ║   ATR expansion deferred to v2 (need bulk 30d bar fetcher). ║
# ║                                                              ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime

from alpaca_data import fetch_snapshots_batched
from config import CONFIG
from universe_committed import load_committed_universe

log = logging.getLogger("decifer.universe_promoter")

_PROMOTED_PATH = os.path.join("data", "daily_promoted.json")


def _catalyst_score_for(ticker: str) -> float:
    """Return the catalyst_score from CatalystEngine.store, or 0.0 if unavailable."""
    try:
        from catalyst_engine import CatalystEngine

        engine = CatalystEngine.get_instance() if hasattr(CatalystEngine, "get_instance") else None
        if engine is None or not hasattr(engine, "store"):
            return 0.0
        cand = engine.store.get_candidate(ticker) if hasattr(engine.store, "get_candidate") else None
        if not cand:
            return 0.0
        return float(cand.get("catalyst_score", 0.0))
    except Exception:
        return 0.0


def _score_row(snap: dict, catalyst_score: float) -> tuple[float, dict]:
    """
    Compute promoter score for one symbol given its snapshot + catalyst score.

    v1 formula:
        score = w_gap      * |gap_pct|        * 100   # scale to ~basis-points
              + w_pm_vol   * pm_vol_ratio
              + w_catalyst * catalyst_score

    where pm_vol_ratio = minute_volume / (prev_volume / 390), i.e. current
    minute-bar volume relative to yesterday's per-minute average. Useful in
    both pre-market (premarket activity is concentrated in minute_bar) and
    intraday (flag unusual last-minute bursts).

    Returns (score, components_dict) so callers can log the reason.
    """
    gap_pct = snap.get("gap_pct") or 0.0
    minute_vol = snap.get("minute_volume", 0)
    prev_vol = snap.get("prev_volume", 0)
    pm_vol_ratio = 0.0
    if prev_vol > 0:
        avg_per_minute = prev_vol / 390.0  # 390 regular-session minutes
        if avg_per_minute > 0:
            pm_vol_ratio = float(minute_vol) / avg_per_minute

    # Clamp pm_vol_ratio to prevent blow-ups on very thin stocks
    pm_vol_ratio = min(pm_vol_ratio, 50.0)

    w_gap = float(CONFIG.get("promoter_weight_gap", 3.0))
    w_pm = float(CONFIG.get("promoter_weight_pm_volume", 2.0))
    w_cat = float(CONFIG.get("promoter_weight_catalyst", 2.0))

    score = w_gap * abs(gap_pct) * 100.0 + w_pm * pm_vol_ratio + w_cat * float(catalyst_score)

    return score, {
        "gap_pct": round(gap_pct, 4),
        "pm_vol_ratio": round(pm_vol_ratio, 2),
        "catalyst_score": round(catalyst_score, 2),
    }


def _reason_for(components: dict) -> str:
    """Human-readable reason tag for log + dashboard."""
    tags = []
    if abs(components.get("gap_pct", 0)) >= 0.02:
        tags.append(f"gap={components['gap_pct']:+.1%}")
    if components.get("pm_vol_ratio", 0) >= 2.0:
        tags.append(f"relvol={components['pm_vol_ratio']:.1f}x")
    if components.get("catalyst_score", 0) >= 5.0:
        tags.append(f"cat={components['catalyst_score']:.1f}")
    return "+".join(tags) if tags else "baseline"


def run_promoter(top_n: int | None = None) -> list[dict]:
    """
    Build the daily promoted list.

    Reads committed_universe.json, snapshots all symbols, scores each, writes
    the top N to data/daily_promoted.json.

    Returns the promoted list. Safe to call at any time — no side effects
    beyond the output JSON file.
    """
    if top_n is None:
        top_n = int(CONFIG.get("promoter_top_n", 50))

    committed = load_committed_universe()
    if not committed:
        log.error("run_promoter: committed_universe is empty — aborting. Run refresh_committed_universe first.")
        return []
    log.info(f"run_promoter: scoring {len(committed)} committed symbols")

    # Snapshot all committed symbols
    snaps = fetch_snapshots_batched(committed, batch_size=100)
    log.info(f"run_promoter: {len(snaps)} snapshots returned")

    # Score each
    scored: list[dict] = []
    for sym in committed:
        snap = snaps.get(sym)
        if not snap:
            continue
        cat = _catalyst_score_for(sym)
        score, components = _score_row(snap, cat)
        scored.append(
            {
                "ticker": sym,
                "score": round(score, 3),
                "gap_pct": components["gap_pct"],
                "pm_vol_ratio": components["pm_vol_ratio"],
                "catalyst_score": components["catalyst_score"],
                "price": snap.get("price"),
                "reason": _reason_for(components),
            }
        )

    scored.sort(key=lambda r: r["score"], reverse=True)
    top = scored[:top_n]

    payload = {
        "promoted_at": datetime.now(UTC).isoformat(),
        "count": len(top),
        "symbols": top,
    }
    os.makedirs(os.path.dirname(_PROMOTED_PATH), exist_ok=True)
    # RB-2: Atomic write — every other persistent write in the codebase uses
    # tempfile + os.replace(). A non-atomic write here produces a corrupt file
    # on interrupted write; the loader returns [] silently, dropping Tier B symbols
    # for up to 18 hours with no warning.
    _dir = os.path.dirname(_PROMOTED_PATH)
    _fd, _tmp = tempfile.mkstemp(dir=_dir, suffix=".tmp")
    try:
        with os.fdopen(_fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(_tmp, _PROMOTED_PATH)
    except Exception:
        try:
            os.unlink(_tmp)
        except OSError:
            pass
        raise
    log.info(
        f"run_promoter: wrote {_PROMOTED_PATH} — top score={top[0]['score']:.2f} "
        f"({top[0]['ticker']}: {top[0]['reason']})"
        if top
        else f"run_promoter: wrote {_PROMOTED_PATH} — empty (no symbols scored)"
    )
    return top


def load_promoted_universe(max_staleness_hours: int | None = None) -> list[str]:
    """
    Load the promoted symbol list. Returns [] if:
      - file missing
      - file malformed
      - file older than max_staleness_hours (default from config: 18)

    Caller (scanner.get_dynamic_universe) is expected to fall back to Tier A
    when this returns [].
    """
    if max_staleness_hours is None:
        max_staleness_hours = int(CONFIG.get("promoted_max_staleness_hours", 18))

    try:
        with open(_PROMOTED_PATH) as f:
            payload = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning(f"load_promoted_universe: {exc}")
        return []

    promoted_at_str = payload.get("promoted_at", "")
    try:
        promoted_at = datetime.fromisoformat(promoted_at_str.replace("Z", "+00:00"))
        age_hours = (datetime.now(UTC) - promoted_at).total_seconds() / 3600.0
        if age_hours > max_staleness_hours:
            log.warning(
                f"load_promoted_universe: file is {age_hours:.1f}h old (>{max_staleness_hours}h) — ignoring"
            )
            return []
    except Exception as exc:
        log.warning(f"load_promoted_universe: timestamp parse failed — {exc}")
        return []

    return [r["ticker"] for r in payload.get("symbols", [])]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    result = run_promoter()
    print(f"\nTop 10: {[(r['ticker'], r['score'], r['reason']) for r in result[:10]]}")
