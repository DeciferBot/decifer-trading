# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  sympathy_scanner.py                        ║
# ║   Sympathy play detection                                    ║
# ║                                                              ║
# ║   When a sector leader has earnings within 2 days, its       ║
# ║   sector peers often move 2-4% in sympathy. This module      ║
# ║   detects the trigger and adds peer tickers to the universe  ║
# ║   so they get scored normally on the next scan cycle.        ║
# ║                                                              ║
# ║   Trigger: sector leader has earnings within 48h.            ║
# ║   Effect:  peer tickers added to scan universe (no special   ║
# ║            bonus — their fundamentals will score naturally). ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import logging
from typing import Sequence

log = logging.getLogger("decifer.sympathy")

# ── Sympathy peer map ─────────────────────────────────────────────────────────
# Leader → peers that typically move in sympathy on earnings catalyst.
# Coverage: ~20 high-impact sector leaders across Tech, Semi, Finance, Energy.
SYMPATHY_MAP: dict[str, list[str]] = {
    # Semiconductors
    "NVDA": ["AMD", "MU", "MRVL", "INTC", "QCOM", "ALAB"],
    "AMD":  ["NVDA", "INTC", "MU", "MRVL"],
    "INTC": ["AMD", "NVDA", "MU"],
    "MU":   ["NVDA", "AMD", "WDC", "MRVL"],
    "QCOM": ["SWKS", "MRVL", "AVGO"],

    # Mega-cap Tech
    "AAPL": ["QCOM", "SWKS", "CRUS", "AMZN"],
    "MSFT": ["ORCL", "CRM", "SNOW", "GOOGL"],
    "GOOGL":["META", "MSFT", "SNAP", "PINS"],
    "META": ["SNAP", "PINS", "GOOGL", "RDDT"],
    "AMZN": ["SHOP", "EBAY", "AAPL"],

    # Cloud / SaaS
    "CRM":  ["SNOW", "PLTR", "ORCL", "MSFT"],
    "SNOW": ["CRM", "PLTR", "DDOG", "MDB"],
    "PLTR": ["SNOW", "CRM", "AI"],

    # EV / Autos
    "TSLA": ["RIVN", "LCID", "F", "GM", "NIO"],

    # Financials
    "JPM":  ["GS", "MS", "BAC", "C", "WFC"],
    "GS":   ["MS", "JPM", "BX", "KKR"],

    # Energy
    "XOM":  ["CVX", "COP", "SLB", "MPC"],
    "CVX":  ["XOM", "COP", "PSX"],

    # Biotech / Healthcare
    "LLY":  ["NVO", "ABBV", "PFE", "MRK"],
    "MRNA": ["BNTX", "PFE", "NVAX"],
}


def get_sympathy_candidates(
    scored_or_universe: Sequence[str],
    earnings_hours: float = 48.0,
) -> list[str]:
    """
    Return peer tickers to add to the universe when a sector leader has
    earnings within *earnings_hours*.

    Parameters
    ----------
    scored_or_universe : symbol strings already in the scan universe
    earnings_hours     : look-ahead window (default 48h covers tomorrow's reports)

    Returns
    -------
    List of peer tickers not already in the universe. Empty on any error.
    """
    from config import CONFIG
    if not CONFIG.get("sympathy_scanner_enabled", True):
        return []

    try:
        from earnings_calendar import get_earnings_within_hours
        universe_set = set(scored_or_universe)

        # Which leaders in our universe have earnings within the window?
        leaders_in_universe = [s for s in SYMPATHY_MAP if s in universe_set]
        if not leaders_in_universe:
            return []

        catalysts = get_earnings_within_hours(leaders_in_universe, hours=earnings_hours)
        if not catalysts:
            return []

        # Collect peers not already in the universe
        new_peers: list[str] = []
        for leader in catalysts:
            peers = SYMPATHY_MAP.get(leader, [])
            for peer in peers:
                if peer not in universe_set and peer not in new_peers:
                    new_peers.append(peer)

        if new_peers:
            log.info(
                "Sympathy scanner: %d catalyst(s) [%s] → adding %d peer(s): %s",
                len(catalysts),
                ", ".join(sorted(catalysts)),
                len(new_peers),
                new_peers,
            )

        return new_peers

    except Exception as exc:
        log.debug("get_sympathy_candidates error (non-blocking): %s", exc)
        return []
