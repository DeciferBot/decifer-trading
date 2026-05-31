"""
conviction_universe.py — Zone management for the conviction funnel.

INTELLIGENCE layer only. No execution imports.
Persists state to data/intelligence/conviction/universe_zones.json.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZONE_TRADEABLE = "TRADEABLE"
ZONE_WAITING_ROOM = "WAITING_ROOM"
ZONE_WATCHLIST = "WATCHLIST"
ZONE_DORMANT = "DORMANT"

ENTER_TRADEABLE = 65
EXIT_TRADEABLE = 50          # hysteresis band: enter at 65, exit at 50
CONSECUTIVE_EXIT_REQUIRED = 2

_DATA_PATH = Path(__file__).parent / "data" / "intelligence" / "conviction" / "universe_zones.json"


def _score_to_zone(score: float) -> str:
    if score >= ENTER_TRADEABLE:
        return ZONE_TRADEABLE
    if score >= 45:
        return ZONE_WAITING_ROOM
    if score >= 25:
        return ZONE_WATCHLIST
    return ZONE_DORMANT


# ---------------------------------------------------------------------------
# Hard-stop evaluation
# ---------------------------------------------------------------------------

def _check_hard_stop(scores: dict) -> Optional[str]:
    """Return a hard-stop reason string, or None if clean.

    Dimension keys expected in scores dict (raw_pts values):
      d1_raw_pts  — analyst consensus
      d5_raw_pts  — primary driver activation
      d7_raw_pts  — unusual put expansion
      d9_raw_pts  — counter-thesis conflicts
      d5_prev_pts — previous cycle d5 (to detect deactivation)
    """
    d1 = scores.get("d1_raw_pts", 0)
    d5 = scores.get("d5_raw_pts", 0)
    d5_prev = scores.get("d5_prev_pts", d5)  # default: no change
    d7 = scores.get("d7_raw_pts", 0)
    d9 = scores.get("d9_raw_pts", 0)

    if d1 < 0:
        return "analyst_consensus_flipped"
    if d9 <= -10:
        return "counter_thesis_confirmed"
    if d5 == 0 and d5_prev > 0:
        return "primary_driver_deactivated"
    if d7 <= -15:
        return "unusual_put_expansion"
    return None


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ZoneEntry:
    zone: str
    entered_at: str
    last_score: float
    consecutive_below_exit: int = 0
    hard_stop: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "zone": self.zone,
            "entered_at": self.entered_at,
            "last_score": self.last_score,
            "consecutive_below_exit": self.consecutive_below_exit,
            "hard_stop": self.hard_stop,
        }

    @staticmethod
    def from_dict(d: dict) -> "ZoneEntry":
        return ZoneEntry(
            zone=d["zone"],
            entered_at=d["entered_at"],
            last_score=d.get("last_score", 0),
            consecutive_below_exit=d.get("consecutive_below_exit", 0),
            hard_stop=d.get("hard_stop"),
        )


@dataclass
class ZoneReport:
    tradeable: list[str]
    waiting_room: list[str]
    watchlist: list[str]
    dormant: list[str]
    rotation_flags: list[dict]
    transitions: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core state
# ---------------------------------------------------------------------------

_state: dict[str, ZoneEntry] = {}
_rotation_flags: list[dict] = []


def load() -> dict:
    """Load persisted zone state from disk. Returns raw dict."""
    if not _DATA_PATH.exists():
        return {"zones": {}, "tradeable": [], "waiting_room": [], "rotation_flags": []}
    with open(_DATA_PATH) as f:
        return json.load(f)


def _load_state() -> None:
    global _state, _rotation_flags
    raw = load()
    _state = {sym: ZoneEntry.from_dict(v) for sym, v in raw.get("zones", {}).items()}
    _rotation_flags = raw.get("rotation_flags", [])


def _persist(updated_at: str) -> None:
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": updated_at,
        "zones": {sym: entry.to_dict() for sym, entry in _state.items()},
        "tradeable": [s for s, e in _state.items() if e.zone == ZONE_TRADEABLE],
        "waiting_room": [s for s, e in _state.items() if e.zone == ZONE_WAITING_ROOM],
        "rotation_flags": _rotation_flags[-50:],  # cap history
    }
    tmp = str(_DATA_PATH) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, _DATA_PATH)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update(scores: dict[str, dict]) -> ZoneReport:
    """Evaluate zone transitions for each symbol and persist.

    Args:
        scores: mapping of symbol -> ConvictionScore.to_dict()
                Must include 'total_score' plus dimension raw_pts keys for
                hard-stop checks.

    Returns:
        ZoneReport with current zone membership and rotation flags.
    """
    _load_state()
    now = datetime.now(timezone.utc).isoformat()
    transitions: list[dict] = []

    for symbol, score_dict in scores.items():
        score = float(score_dict.get("total_score", 0))
        entry = _state.get(symbol)

        # Hard-stop check (only relevant if currently TRADEABLE)
        hard_stop = None
        if entry and entry.zone == ZONE_TRADEABLE:
            hard_stop = _check_hard_stop(score_dict)

        if hard_stop:
            new_zone = _score_to_zone(score)
            transitions.append({
                "symbol": symbol, "from": ZONE_TRADEABLE,
                "to": new_zone, "reason": hard_stop, "ts": now,
            })
            _rotation_flags.append({
                "symbol": symbol, "reason": hard_stop,
                "score": score, "consecutive": 0, "ts": now,
            })
            _state[symbol] = ZoneEntry(
                zone=new_zone, entered_at=now,
                last_score=score, consecutive_below_exit=0,
                hard_stop=hard_stop,
            )
            continue

        if entry is None:
            # New symbol — assign initial zone
            _state[symbol] = ZoneEntry(
                zone=_score_to_zone(score), entered_at=now, last_score=score,
            )
            continue

        prev_zone = entry.zone

        if prev_zone == ZONE_TRADEABLE:
            if score < EXIT_TRADEABLE:
                consecutive = entry.consecutive_below_exit + 1
                entry.consecutive_below_exit = consecutive
                entry.last_score = score
                if consecutive >= CONSECUTIVE_EXIT_REQUIRED:
                    new_zone = _score_to_zone(score)
                    transitions.append({
                        "symbol": symbol, "from": ZONE_TRADEABLE,
                        "to": new_zone, "reason": "score_drift", "ts": now,
                    })
                    _rotation_flags.append({
                        "symbol": symbol, "reason": "score_drift",
                        "score": score, "consecutive": consecutive, "ts": now,
                    })
                    _state[symbol] = ZoneEntry(
                        zone=new_zone, entered_at=now, last_score=score,
                    )
                # else: still TRADEABLE, just counting down
            else:
                # Score healthy — reset counter
                entry.consecutive_below_exit = 0
                entry.last_score = score
        else:
            # Not in TRADEABLE: transition purely by score thresholds
            new_zone = _score_to_zone(score)
            if new_zone != prev_zone:
                transitions.append({
                    "symbol": symbol, "from": prev_zone,
                    "to": new_zone, "reason": "score_change", "ts": now,
                })
                _state[symbol] = ZoneEntry(
                    zone=new_zone, entered_at=now, last_score=score,
                )
            else:
                entry.last_score = score

    _persist(now)

    return ZoneReport(
        tradeable=get_tradeable(),
        waiting_room=get_waiting_room(),
        watchlist=[s for s, e in _state.items() if e.zone == ZONE_WATCHLIST],
        dormant=[s for s, e in _state.items() if e.zone == ZONE_DORMANT],
        rotation_flags=get_rotation_flags(),
        transitions=transitions,
    )


def get_tradeable() -> list[str]:
    """Return symbols currently in TRADEABLE zone."""
    return [s for s, e in _state.items() if e.zone == ZONE_TRADEABLE]


def get_waiting_room() -> list[str]:
    """Return symbols currently in WAITING_ROOM zone."""
    return [s for s, e in _state.items() if e.zone == ZONE_WAITING_ROOM]


def get_rotation_flags() -> list[dict]:
    """Return recent rotation flag events."""
    return list(_rotation_flags)
