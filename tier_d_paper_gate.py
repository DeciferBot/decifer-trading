"""
tier_d_paper_gate.py — Tier D paper POSITION entry gate.

Single responsibility: determine if a Tier D POSITION signal is eligible for
a paper trade. Called from entry_gate.validate_entry() before the shadow-mode
block. The gate result is cached in _GATE_RESULTS[symbol] and consumed once by
signal_dispatcher.py (to apply the size fraction and add tagging kwargs before
calling execute_buy).

Safety guarantees:
- is_paper_mode() is checked FIRST. If the active IBKR account is not the
  configured paper account, the gate returns tier_d_live_disabled immediately
  regardless of all other conditions.
- Tactical Momentum is always blocked (shadow-only).
- Options/LEAPS are always blocked (long equity only).
- Daily cap, open cap, and duplicate-symbol checks read from event_log.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date

from config import CONFIG

log = logging.getLogger("decifer.tier_d_paper_gate")

# Module-level cache: symbol → last evaluate() result.
# Consumed once by signal_dispatcher via get_result().
_GATE_RESULTS: dict[str, dict] = {}


def is_paper_mode() -> bool:
    """Return True if the active IBKR account is the configured paper account."""
    active = CONFIG.get("active_account", "")
    paper = CONFIG.get("accounts", {}).get("paper", "DUP481326")
    return bool(active and active == paper)


def evaluate(
    symbol: str,
    universe_bucket: str | None,
    primary_archetype: str | None,
    discovery_score: float | int | None,
    instrument: str | None,
) -> dict:
    """
    Evaluate whether a Tier D POSITION signal may enter a paper trade.

    Returns a dict with keys:
      paper_entry_allowed     (bool)
      paper_entry_block_reason (str | None)
      position_size_bucket    (str)

    Side effect: stores the result in _GATE_RESULTS[symbol] so
    signal_dispatcher can retrieve it via get_result() after validate_entry
    returns True.
    """
    cfg = CONFIG.get("entry_gate", {})

    def _ok() -> dict:
        r = {
            "paper_entry_allowed": True,
            "paper_entry_block_reason": None,
            "position_size_bucket": "tier_d_paper_starter",
        }
        _GATE_RESULTS[symbol] = r
        return r

    def _block(reason: str) -> dict:
        r = {
            "paper_entry_allowed": False,
            "paper_entry_block_reason": reason,
            "position_size_bucket": "",
        }
        _GATE_RESULTS[symbol] = r
        return r

    # 1. Live lock — checked unconditionally first
    if not is_paper_mode():
        return _block("tier_d_live_disabled")

    # 2. Config master switch
    if not cfg.get("position_research_allow_paper_entries", False):
        return _block("paper_entries_disabled")

    # 3. Tactical Momentum is always shadow-only
    if universe_bucket == "tactical_momentum":
        return _block("tactical_momentum_shadow_only")

    # 4. Core Research bucket required
    if universe_bucket != "core_research":
        return _block(f"non_core_bucket:{universe_bucket}")

    # 5. Primary archetype must be set
    if cfg.get("position_research_paper_require_archetype", True) and not primary_archetype:
        return _block("no_primary_archetype")

    # 6. Discovery score floor
    min_ds = cfg.get("position_research_paper_min_discovery_score", 8)
    if (discovery_score or 0) < min_ds:
        return _block(f"discovery_score_below_min:{discovery_score}<{min_ds}")

    # 7. Long equity only — no options or LEAPS
    if instrument and instrument.lower() in ("call", "put", "option", "leaps"):
        return _block("options_blocked")

    # 8. Daily cap, open position cap, duplicate-symbol check
    max_open = cfg.get("position_research_paper_max_open_positions", 5)
    max_per_day = cfg.get("position_research_paper_max_entries_per_day", 3)
    try:
        from event_log import open_trades as _open_trades

        tier_d_open = [
            t for t in _open_trades().values() if t.get("tier_d_paper_entry")
        ]
        open_syms = {t.get("symbol", "").upper() for t in tier_d_open}

        if symbol.upper() in open_syms:
            return _block("duplicate_symbol_open")

        if len(tier_d_open) >= max_open:
            return _block(f"open_position_cap:{len(tier_d_open)}>={max_open}")

        daily = _count_tier_d_paper_today()
        if daily >= max_per_day:
            return _block(f"daily_cap:{daily}>={max_per_day}")

    except Exception as exc:
        log.debug("tier_d_paper_gate: cap checks failed (permissive): %s", exc)

    return _ok()


def get_result(symbol: str) -> dict | None:
    """Retrieve and clear the stored gate result for symbol (consumed once)."""
    return _GATE_RESULTS.pop(symbol, None)


def _count_tier_d_paper_today() -> int:
    """Count ORDER_INTENT records with tier_d_paper_entry=True filed today."""
    today = date.today().isoformat()
    log_path = os.path.join(CONFIG.get("data_dir", "data"), "trade_events.jsonl")
    count = 0
    try:
        with open(log_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if (
                    rec.get("event") == "ORDER_INTENT"
                    and rec.get("tier_d_paper_entry")
                    and (rec.get("ts") or "").startswith(today)
                ):
                    count += 1
    except FileNotFoundError:
        pass
    return count
