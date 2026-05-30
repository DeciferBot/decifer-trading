# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  options_flow_engine.py                    ║
# ║   Single responsibility: accumulate option prints and        ║
# ║   detect unusual flow signals from rolling windows.          ║
# ║                                                              ║
# ║   Exposed API:                                               ║
# ║     FlowPrint, FlowEvent — data contracts                   ║
# ║     UnderlyingWindow     — rolling 30-min accumulator        ║
# ║     parse_occ()          — OCC symbol parser                 ║
# ║     detect_events()      — threshold evaluator               ║
# ║     DRIVER_TAGS          — symbol → driver mapping           ║
# ║                                                              ║
# ║   No trading logic. No execution. No broker. Data only.      ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# ── OCC parsing ───────────────────────────────────────────────────────────────
_OCC_RE = re.compile(r"^([A-Z ]{1,6})(\d{6})([CP])(\d{8})$")


def parse_occ(sym: str) -> tuple[str, str, str, float] | None:
    """Parse OCC option symbol → (underlying, expiry YYYY-MM-DD, side C/P, strike).

    Returns None if the symbol does not match the OCC format.
    """
    m = _OCC_RE.match(sym.strip())
    if not m:
        return None
    underlying = m.group(1).strip()
    try:
        expiry = datetime.strptime(m.group(2), "%y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None
    side = m.group(3)
    strike = int(m.group(4)) / 1000.0
    return underlying, expiry, side, strike


# ── Detection thresholds ─────────────────────────────────────────────────────
WINDOW_MINUTES = 30           # rolling accumulation window
CLUSTER_WINDOW_MINUTES = 15   # tighter window for strike clustering

MIN_SWEEP_SIZE = 50           # contracts per single print to qualify as sweep
SWEEP_TOLERANCE = 0.01        # price may be ≤ $0.01 below ask and still count as sweep

MIN_SWEEP_COUNT = 2           # sweeps in the window to fire a SWEEP event
MIN_CLUSTER_PRINTS = 3        # same-strike prints within cluster window
MIN_CLUSTER_TOTAL = 100       # total contracts across cluster prints
MIN_CROSS_EXPIRY_COUNT = 3    # distinct expiries with activity to fire CROSS_EXPIRY
MIN_CROSS_EXPIRY_TOTAL = 150  # total contracts across expiries

# ── Driver tag mapping (underlying → intelligence layer drivers) ──────────────
# Derived directly from live_driver_resolver.py sensor logic.
DRIVER_TAGS: dict[str, list[str]] = {
    "SMH":  ["ai_capex_growth"],
    "NVDA": ["ai_compute_demand", "ai_capex_growth"],
    "AMD":  ["ai_compute_demand"],
    "TSM":  ["ai_capex_growth"],
    "AVGO": ["ai_capex_growth"],
    "IEF":  ["yields_rising", "yields_falling"],
    "TLT":  ["yields_rising", "yields_falling"],
    "SHY":  ["yields_rising", "yields_falling"],
    "USO":  ["oil_supply_shock"],
    "XOM":  ["oil_supply_shock"],
    "CVX":  ["oil_supply_shock"],
    "OXY":  ["oil_supply_shock"],
    "ITA":  ["geopolitical_risk_rising"],
    "LMT":  ["geopolitical_risk_rising"],
    "RTX":  ["geopolitical_risk_rising"],
    "NOC":  ["geopolitical_risk_rising"],
    "GD":   ["geopolitical_risk_rising"],
    "HYG":  ["credit_stress_rising", "credit_stress_easing"],
    "LQD":  ["credit_stress_rising", "credit_stress_easing"],
    "JNK":  ["credit_stress_rising"],
    "UVXY": ["risk_off_rotation"],
    "VXX":  ["risk_off_rotation"],
    "SPY":  ["risk_off_rotation", "risk_on_rotation"],
    "QQQ":  ["risk_on_rotation"],
    "GLD":  ["gold_safe_haven_bid"],
    "IAU":  ["gold_safe_haven_bid"],
    "IWM":  ["small_cap_risk_on"],
    "IJR":  ["small_cap_risk_on"],
}


# ── Data contracts ────────────────────────────────────────────────────────────

@dataclass
class FlowPrint:
    ts: datetime
    occ_symbol: str
    underlying: str
    side: str          # "C" or "P"
    expiry: str        # "YYYY-MM-DD"
    strike: float
    price: float
    size: float
    ask: float | None  # best ask from quote cache at print time; None if unavailable
    is_sweep: bool     # price >= ask - SWEEP_TOLERANCE and size >= MIN_SWEEP_SIZE


@dataclass
class FlowEvent:
    ts: str            # ISO UTC
    underlying: str
    signal_type: str   # "SWEEP" | "CLUSTER" | "CROSS_EXPIRY"
    side: str          # "CALL" | "PUT" | "MIXED"
    contracts: int
    strike: float | None
    expiry: str | None
    price: float | None
    ask_at_print: float | None
    is_sweep: bool
    sweep_count: int
    cluster_count: int
    expiry_count: int
    driver_tags: list[str]
    score: int         # urgency score 0–100


# ── Rolling accumulator ───────────────────────────────────────────────────────

class UnderlyingWindow:
    """Rolling WINDOW_MINUTES accumulator for option prints on one underlying."""

    __slots__ = ("symbol", "prints")

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.prints: list[FlowPrint] = []

    def add(self, p: FlowPrint) -> None:
        self.prints.append(p)
        self._evict()

    def _evict(self) -> None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=WINDOW_MINUTES)
        self.prints = [p for p in self.prints if p.ts >= cutoff]

    def calls(self) -> list[FlowPrint]:
        return [p for p in self.prints if p.side == "C"]

    def puts(self) -> list[FlowPrint]:
        return [p for p in self.prints if p.side == "P"]


# ── Event detection ───────────────────────────────────────────────────────────

def detect_events(window: UnderlyingWindow) -> list[FlowEvent]:
    """Evaluate a window and return newly detectable flow events."""
    driver_tags = DRIVER_TAGS.get(window.symbol, [])
    events: list[FlowEvent] = []

    _detect_sweeps(window, driver_tags, events)
    _detect_clusters(window, driver_tags, events)
    _detect_cross_expiry(window, driver_tags, events)

    return events


def _detect_sweeps(window: UnderlyingWindow, tags: list[str], out: list[FlowEvent]) -> None:
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for side_label, prints in (("CALL", window.calls()), ("PUT", window.puts())):
        sweeps = [p for p in prints if p.is_sweep]
        if len(sweeps) < MIN_SWEEP_COUNT:
            continue
        total = int(sum(p.size for p in sweeps))
        latest = max(sweeps, key=lambda p: p.ts)
        score = min(100, 40 + len(sweeps) * 10 + total // 100)
        out.append(FlowEvent(
            ts=now_iso, underlying=window.symbol,
            signal_type="SWEEP", side=side_label, contracts=total,
            strike=latest.strike, expiry=latest.expiry,
            price=latest.price, ask_at_print=latest.ask, is_sweep=True,
            sweep_count=len(sweeps), cluster_count=0,
            expiry_count=len({p.expiry for p in sweeps}),
            driver_tags=tags, score=score,
        ))


def _detect_clusters(window: UnderlyingWindow, tags: list[str], out: list[FlowEvent]) -> None:
    now = datetime.now(tz=timezone.utc)
    now_iso = now.isoformat()
    cluster_cutoff = now - timedelta(minutes=CLUSTER_WINDOW_MINUTES)

    for side_label, prints in (("CALL", window.calls()), ("PUT", window.puts())):
        recent = [p for p in prints if p.ts >= cluster_cutoff]
        buckets: dict[float, list[FlowPrint]] = {}
        for p in recent:
            buckets.setdefault(p.strike, []).append(p)

        for strike, group in buckets.items():
            if len(group) < MIN_CLUSTER_PRINTS:
                continue
            total = int(sum(p.size for p in group))
            if total < MIN_CLUSTER_TOTAL:
                continue
            latest = max(group, key=lambda p: p.ts)
            score = min(100, 50 + len(group) * 5 + total // 50)
            out.append(FlowEvent(
                ts=now_iso, underlying=window.symbol,
                signal_type="CLUSTER", side=side_label, contracts=total,
                strike=strike, expiry=latest.expiry,
                price=latest.price, ask_at_print=latest.ask, is_sweep=False,
                sweep_count=sum(1 for p in group if p.is_sweep),
                cluster_count=len(group),
                expiry_count=len({p.expiry for p in group}),
                driver_tags=tags, score=score,
            ))


def _detect_cross_expiry(window: UnderlyingWindow, tags: list[str], out: list[FlowEvent]) -> None:
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    all_prints = window.prints
    expiry_buckets: dict[str, list[FlowPrint]] = {}
    for p in all_prints:
        expiry_buckets.setdefault(p.expiry, []).append(p)

    if len(expiry_buckets) < MIN_CROSS_EXPIRY_COUNT:
        return
    total = int(sum(p.size for p in all_prints))
    if total < MIN_CROSS_EXPIRY_TOTAL:
        return

    call_total = int(sum(p.size for p in window.calls()))
    put_total = int(sum(p.size for p in window.puts()))
    if call_total > put_total * 1.5:
        side = "CALL"
    elif put_total > call_total * 1.5:
        side = "PUT"
    else:
        side = "MIXED"

    score = min(100, 60 + len(expiry_buckets) * 5)
    out.append(FlowEvent(
        ts=now_iso, underlying=window.symbol,
        signal_type="CROSS_EXPIRY", side=side, contracts=total,
        strike=None, expiry=None, price=None, ask_at_print=None,
        is_sweep=False, sweep_count=0, cluster_count=0,
        expiry_count=len(expiry_buckets),
        driver_tags=tags, score=score,
    ))
